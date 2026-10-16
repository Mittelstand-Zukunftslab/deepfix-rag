"""Passage and answer votes, the MLflow outbox, and the feedback dataset."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Literal
import unicodedata
import uuid

from deepfix_rag.config.settings import LOGGER
from deepfix_rag.interactions import get_interaction, init_interactions
from deepfix_rag.storage import connect
from deepfix_rag.tracing import FEEDBACK_EXPERIMENT, PRODUCT_EXPERIMENT

UNSYNCED_AFTER = timedelta(minutes=5)
COMMENT_MAX_LENGTH = 1000
_SPACE = re.compile(r"\s+")


class CommentReason(StrEnum):
    """One-tap reasons offered after a thumbs-down."""

    WRONG = "wrong"
    INCOMPLETE = "incomplete"
    IGNORED_DOCUMENTS = "ignored_documents"
    NOT_RELEVANT = "not_relevant"
    OUTDATED = "outdated"
    OTHER = "other"


COMMENT_REASONS: dict[str, frozenset[CommentReason]] = {
    "answer": frozenset(
        {
            CommentReason.WRONG,
            CommentReason.INCOMPLETE,
            CommentReason.IGNORED_DOCUMENTS,
            CommentReason.OTHER,
        }
    ),
    "passage": frozenset(
        {CommentReason.NOT_RELEVANT, CommentReason.OUTDATED, CommentReason.OTHER}
    ),
}


class UnknownInteractionError(Exception):
    """No stored interaction matches this id."""

    def __init__(self) -> None:
        super().__init__("No interaction with that id.")


class UnknownPassageError(Exception):
    """The rank is not in the interaction's hit snapshot."""

    def __init__(self) -> None:
        super().__init__("That passage is not in this result.")


class AnswerMissingError(Exception):
    """The interaction has no generated answer to vote on."""

    def __init__(self) -> None:
        super().__init__("There is no answer to vote on.")


class NoVoteError(Exception):
    """A comment needs a current vote on the same answer or passage."""

    def __init__(self) -> None:
        super().__init__("Vote before adding a comment.")


class InvalidCommentError(Exception):
    """The reason does not fit the vote, or the comment is empty."""


@dataclass(frozen=True)
class FeedbackComment:
    """One comment, stored with the vote it was written for."""

    comment_id: str
    interaction_id: str
    target: str
    rank: int | None
    vote: Literal["up", "down"]
    reason: CommentReason | None
    text: str
    created_at: str


@dataclass(frozen=True)
class FeedbackQuestion:
    """One normalized question and its document labels."""

    query_id: str
    query: str
    doc_ids: list[str]
    negative_doc_ids: list[str]
    holdout: bool

    def row(self) -> dict[str, object]:
        """Eval row. Answer text is never a label."""
        return {
            "query_id": self.query_id,
            "query": self.query,
            "doc_ids": self.doc_ids,
            "negative_doc_ids": self.negative_doc_ids,
        }


@dataclass(frozen=True)
class ConfigApproval:
    """Answer approval for one champion config."""

    config_path: str
    up: int
    down: int
    rate: float | None


@dataclass(frozen=True)
class FeedbackSummary:
    """Counts an admin uses to see whether feedback is ready."""

    passage_up: int
    passage_down: int
    answer_up: int
    answer_down: int
    dev_question_count: int
    holdout_question_count: int
    answer_approval_rate: float | None
    answer_approval_by_config: list[ConfigApproval]
    unsynced_event_count: int
    no_trace_event_count: int
    product_experiment_url: str | None
    feedback_experiment_url: str | None


@dataclass(frozen=True)
class ExportResult:
    """A snapshot. Holdout stays in this object and is not written anywhere."""

    snapshot_id: str
    dev: list[FeedbackQuestion]
    holdout: list[FeedbackQuestion]
    dev_path: Path
    run_id: str | None


def init_feedback(db_path: Path) -> None:
    """Create the append-only vote log and the mirror of MLflow assessment ids."""
    init_interactions(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_events (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                target TEXT NOT NULL CHECK(target IN ('passage', 'answer')),
                rank INTEGER,
                action TEXT NOT NULL CHECK(action IN ('up', 'down', 'clear')),
                doc_id TEXT,
                passage_key TEXT,
                passage_text TEXT,
                created_at TEXT NOT NULL,
                sync_status TEXT NOT NULL CHECK(sync_status IN ('pending', 'synced', 'no_trace')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_mirrors (
                user_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                target TEXT NOT NULL,
                rank INTEGER NOT NULL,
                assessment_id TEXT,
                PRIMARY KEY (user_id, interaction_id, target, rank)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_comments (
                comment_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                target TEXT NOT NULL CHECK(target IN ('passage', 'answer')),
                rank INTEGER,
                vote TEXT NOT NULL CHECK(vote IN ('up', 'down')),
                reason TEXT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS feedback_comments_by_interaction
            ON feedback_comments (interaction_id)
            """
        )


def record_vote(
    db_path: Path,
    *,
    user_id: str,
    interaction_id: str,
    target: str,
    action: str,
    rank: int | None = None,
) -> Literal["up", "down"] | None:
    """Append one vote event and return the user's current vote. Does not call MLflow.

    Users vote only on their own interactions; anyone else's is reported as unknown.
    """
    init_feedback(db_path)
    interaction = get_interaction(db_path, interaction_id)
    if interaction is None or interaction.user_id != user_id:
        raise UnknownInteractionError
    doc_id = None
    passage_key = None
    passage_text = None
    stored_rank = rank
    if target == "passage":
        if rank is None:
            raise UnknownPassageError
        hit = next((item for item in interaction.hits if item.rank == rank), None)
        if hit is None:
            raise UnknownPassageError
        doc_id = hit.doc_id
        passage_key = hit.passage_key
        passage_text = hit.text
    elif target == "answer":
        if interaction.kind != "ask" or not interaction.answer:
            raise AnswerMissingError
        stored_rank = None
    else:
        raise UnknownPassageError
    moment = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback_events (
                event_id, user_id, interaction_id, target, rank, action,
                doc_id, passage_key, passage_text, created_at, sync_status,
                attempt_count, next_attempt_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                uuid.uuid4().hex,
                user_id,
                interaction_id,
                target,
                stored_rank,
                action,
                doc_id,
                passage_key,
                passage_text,
                moment,
                moment,
            ),
        )
    answer_vote, passage_votes = current_votes(db_path, user_id, interaction_id)
    if target == "answer":
        return answer_vote
    return passage_votes.get(rank or 0)


def current_votes(
    db_path: Path,
    user_id: str,
    interaction_id: str,
) -> tuple[Literal["up", "down"] | None, dict[int, Literal["up", "down"]]]:
    """Return this user's latest answer vote and passage votes by rank."""
    init_feedback(db_path)
    answer_vote = None
    passages: dict[int, str] = {}
    for row in _latest_events(db_path, user_id=user_id, interaction_id=interaction_id):
        if row["action"] == "clear":
            continue
        if row["target"] == "answer":
            answer_vote = row["action"]
        elif row["rank"] is not None:
            passages[int(row["rank"])] = row["action"]
    return answer_vote, passages


def save_comment(
    db_path: Path,
    *,
    user_id: str,
    interaction_id: str,
    target: str,
    rank: int | None = None,
    reason: CommentReason | None = None,
    text: str = "",
) -> FeedbackComment:
    """Append a comment to the user's current vote and queue it for MLflow.

    Comments are append-only and are never used as eval labels.
    """
    init_feedback(db_path)
    interaction = get_interaction(db_path, interaction_id)
    if interaction is None or interaction.user_id != user_id:
        raise UnknownInteractionError
    if target == "passage":
        if rank is None or all(hit.rank != rank for hit in interaction.hits):
            raise UnknownPassageError
    elif target == "answer":
        rank = None
    else:
        raise UnknownPassageError
    answer_vote, passage_votes = current_votes(db_path, user_id, interaction_id)
    vote = answer_vote if target == "answer" else passage_votes.get(rank or 0)
    if vote is None:
        raise NoVoteError
    if reason is not None and (vote != "down" or reason not in COMMENT_REASONS[target]):
        raise InvalidCommentError("That reason does not fit this vote.")
    body = text.strip()
    if len(body) > COMMENT_MAX_LENGTH:
        raise InvalidCommentError(f"Keep the comment under {COMMENT_MAX_LENGTH} characters.")
    if reason is None and not body:
        raise InvalidCommentError("Choose a reason or write a comment.")
    comment = FeedbackComment(
        comment_id=uuid.uuid4().hex,
        interaction_id=interaction_id,
        target=target,
        rank=rank,
        vote=vote,
        reason=reason,
        text=body,
        created_at=_now(),
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback_comments (
                comment_id, user_id, interaction_id, target, rank, vote, reason, text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comment.comment_id,
                user_id,
                interaction_id,
                target,
                rank,
                vote,
                reason,
                body,
                comment.created_at,
            ),
        )
        conn.execute(
            """
            UPDATE feedback_events
            SET sync_status = 'pending', next_attempt_at = ?
            WHERE event_id = (
                SELECT event_id FROM feedback_events
                WHERE user_id = ? AND interaction_id = ? AND target = ?
                  AND COALESCE(rank, -1) = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
            )
              AND sync_status = 'synced'
            """,
            (comment.created_at, user_id, interaction_id, target, -1 if rank is None else rank),
        )
    return comment


def comments_for(db_path: Path, interaction_id: str) -> list[FeedbackComment]:
    """Every comment on one interaction, oldest first."""
    init_feedback(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM feedback_comments
            WHERE interaction_id = ?
            ORDER BY created_at, rowid
            """,
            (interaction_id,),
        ).fetchall()
    return [
        FeedbackComment(
            comment_id=row["comment_id"],
            interaction_id=row["interaction_id"],
            target=row["target"],
            rank=row["rank"],
            vote=row["vote"],
            reason=CommentReason(row["reason"]) if row["reason"] else None,
            text=row["text"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def feedback_summary(db_path: Path) -> FeedbackSummary:
    """Count current votes, labeled questions, and outbox problems."""
    init_feedback(db_path)
    passage_up = passage_down = answer_up = answer_down = 0
    approval: dict[str, list[int]] = {}
    for row in _latest_events(db_path):
        if row["action"] == "clear":
            continue
        if row["target"] == "passage" and row["action"] == "up":
            passage_up += 1
        elif row["target"] == "passage" and row["action"] == "down":
            passage_down += 1
        elif row["target"] == "answer" and row["action"] == "up":
            answer_up += 1
            approval.setdefault(row["config_path"], [0, 0])[0] += 1
        elif row["target"] == "answer" and row["action"] == "down":
            answer_down += 1
            approval.setdefault(row["config_path"], [0, 0])[1] += 1
    questions = build_questions(db_path)
    cutoff = (datetime.now(timezone.utc) - UNSYNCED_AFTER).isoformat()
    with connect(db_path) as conn:
        unsynced = conn.execute(
            """
            SELECT COUNT(*) AS n FROM feedback_events
            WHERE sync_status = 'pending' AND created_at <= ?
            """,
            (cutoff,),
        ).fetchone()["n"]
        no_trace = conn.execute(
            "SELECT COUNT(*) AS n FROM feedback_events WHERE sync_status = 'no_trace'"
        ).fetchone()["n"]
    by_config = [
        ConfigApproval(
            config_path=path,
            up=counts[0],
            down=counts[1],
            rate=_rate(counts[0], counts[1]),
        )
        for path, counts in sorted(approval.items())
    ]
    return FeedbackSummary(
        passage_up=passage_up,
        passage_down=passage_down,
        answer_up=answer_up,
        answer_down=answer_down,
        dev_question_count=sum(1 for question in questions if not question.holdout),
        holdout_question_count=sum(1 for question in questions if question.holdout),
        answer_approval_rate=_rate(answer_up, answer_down),
        answer_approval_by_config=by_config,
        unsynced_event_count=int(unsynced),
        no_trace_event_count=int(no_trace),
        product_experiment_url=_experiment_url(PRODUCT_EXPERIMENT),
        feedback_experiment_url=_experiment_url(FEEDBACK_EXPERIMENT),
    )


def build_questions(db_path: Path) -> list[FeedbackQuestion]:
    """Turn current passage votes into dev and holdout questions. Nothing is written."""
    init_feedback(db_path)
    deleted = _deleted_ids(db_path)
    nets: dict[tuple[str, str, str], list[int]] = {}
    for row in _latest_events(db_path):
        if row["target"] != "passage" or row["action"] == "clear":
            continue
        if not row["passage_key"] or not row["doc_id"]:
            continue
        key = (normalize_query(row["query"]), row["doc_id"], row["passage_key"])
        counts = nets.setdefault(key, [0, 0])
        if row["action"] == "up":
            counts[0] += 1
        elif row["action"] == "down":
            counts[1] += 1
    relevant: dict[str, set[str]] = {}
    negative: dict[str, set[str]] = {}
    for (query, doc_id, _passage_key), (up, down) in nets.items():
        if doc_id in deleted:
            continue
        net = up - down
        if net > 0:
            relevant.setdefault(query, set()).add(doc_id)
            negative.setdefault(query, set()).discard(doc_id)
        elif net < 0 and doc_id not in relevant.get(query, set()):
            negative.setdefault(query, set()).add(doc_id)
    questions = []
    for query in sorted(set(relevant) | set(negative)):
        doc_ids = sorted(relevant.get(query, set()))
        negative_ids = sorted(negative.get(query, set()) - set(doc_ids))
        if not doc_ids and not negative_ids:
            continue
        questions.append(
            FeedbackQuestion(
                query_id=query_id_for(query),
                query=query,
                doc_ids=doc_ids,
                negative_doc_ids=negative_ids,
                holdout=is_holdout(query),
            )
        )
    return questions


def export_feedback(db_path: Path, workspace: Path) -> ExportResult:
    """Write the dev split and log it. Holdout is returned in memory only."""
    questions = build_questions(db_path)
    dev = [question for question in questions if not question.holdout]
    holdout = [question for question in questions if question.holdout]
    snapshot_id = uuid.uuid4().hex
    directory = workspace / "feedback" / snapshot_id
    directory.mkdir(parents=True, exist_ok=True)
    dev_path = directory / "feedback_dev.jsonl"
    dev_path.write_text(
        "".join(json.dumps(question.row(), ensure_ascii=False) + "\n" for question in dev),
        encoding="utf-8",
    )
    downvotes_path = directory / "answer_downvotes.jsonl"
    downvotes_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in _answer_downvotes(db_path)),
        encoding="utf-8",
    )
    holdout_ids = "\n".join(sorted(question.query_id for question in holdout))
    run_id = _log_export(
        dev_path,
        downvotes_path,
        dev_count=len(dev),
        holdout_count=len(holdout),
        snapshot_id=snapshot_id,
        holdout_ids_sha256=hashlib.sha256(holdout_ids.encode("utf-8")).hexdigest(),
    )
    return ExportResult(
        snapshot_id=snapshot_id,
        dev=dev,
        holdout=holdout,
        dev_path=dev_path,
        run_id=run_id,
    )


def normalize_query(query: str) -> str:
    """NFKC, trim, lowercase, and collapse whitespace."""
    text = unicodedata.normalize("NFKC", query).strip().lower()
    return _SPACE.sub(" ", text)


def query_id_for(normalized: str) -> str:
    """Stable id: fb_ plus the first 12 hex characters of the query hash."""
    return "fb_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def is_holdout(normalized: str) -> bool:
    """Twenty percent of queries, stable for the life of the normalized text."""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return int(digest, 16) % 100 < 20


def sync_pending(db_path: Path, *, limit: int = 20) -> int:
    """Mirror due vote events once. Failures stay pending and retry later."""
    init_feedback(db_path)
    claimed = _claim_due(db_path, limit)
    synced = 0
    for row in claimed:
        try:
            _mirror_event(db_path, row)
        except Exception:
            LOGGER.exception("Could not mirror feedback event %s", row["event_id"])
            _retry_later(db_path, row["event_id"], int(row["attempt_count"]))
        else:
            synced += 1
    return synced


class FeedbackWorker:
    """Retry unsynced votes in the background. The vote route does not wait for it."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="feedback-outbox", daemon=True)

    def start(self) -> None:
        """Begin retrying. A second start is ignored."""
        if self._thread.is_alive():
            return
        init_feedback(self.db_path)
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to exit. An in-flight MLflow call is not interrupted."""
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                sync_pending(self.db_path)
            except Exception:
                LOGGER.exception("Feedback outbox failed")
            self._stop.wait(0.5)


def _latest_events(
    db_path: Path, *, user_id: str | None = None, interaction_id: str | None = None
):
    init_feedback(db_path)
    filters = ["1 = 1"]
    params: list[str] = []
    if user_id is not None:
        filters.append("e.user_id = ?")
        params.append(user_id)
    if interaction_id is not None:
        filters.append("e.interaction_id = ?")
        params.append(interaction_id)
    where = " AND ".join(filters)
    with connect(db_path) as conn:
        return conn.execute(
            f"""
            SELECT e.event_id, e.user_id, e.interaction_id, e.target, e.rank, e.action,
                   e.doc_id, e.passage_key, e.passage_text, e.created_at, e.sync_status,
                   e.attempt_count, i.query, i.config_path, i.answer, i.trace_id
            FROM feedback_events AS e
            JOIN interactions AS i ON i.interaction_id = e.interaction_id
            WHERE {where}
              AND e.rowid = (
                    SELECT e2.rowid FROM feedback_events AS e2
                    WHERE e2.user_id = e.user_id
                      AND e2.interaction_id = e.interaction_id
                      AND e2.target = e.target
                      AND COALESCE(e2.rank, -1) = COALESCE(e.rank, -1)
                    ORDER BY e2.created_at DESC, e2.rowid DESC
                    LIMIT 1
              )
            ORDER BY e.created_at
            """,
            params,
        ).fetchall()


def _claim_due(db_path: Path, limit: int):
    moment = _now()
    claimed_until = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM feedback_events
            WHERE sync_status = 'pending' AND next_attempt_at <= ?
            ORDER BY created_at
            LIMIT ?
            """,
            (moment, limit),
        ).fetchall()
        conn.executemany(
            """
            UPDATE feedback_events
            SET next_attempt_at = ?
            WHERE event_id = ? AND sync_status = 'pending'
            """,
            [(claimed_until, row["event_id"]) for row in rows],
        )
    return rows


def _mirror_event(db_path: Path, row) -> None:
    interaction = get_interaction(db_path, row["interaction_id"])
    trace_id = None if interaction is None else interaction.trace_id
    if not trace_id or not _trace_exists(trace_id):
        _set_status(db_path, row["event_id"], "no_trace")
        return
    import mlflow
    from mlflow.entities.assessment_source import AssessmentSource, AssessmentSourceType

    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id=row["user_id"])
    rank_key = -1 if row["rank"] is None else int(row["rank"])
    assessment_id = _assessment_id(
        db_path, row["user_id"], row["interaction_id"], row["target"], rank_key
    )
    if row["action"] == "clear":
        if assessment_id:
            mlflow.delete_assessment(trace_id, assessment_id)
            _save_assessment(db_path, row, rank_key, None)
        _set_status(db_path, row["event_id"], "synced")
        return
    metadata: dict[str, object] = (
        {} if row["target"] == "answer" else {"doc_id": row["doc_id"], "rank": int(row["rank"])}
    )
    comment = _latest_comment(db_path, row, rank_key)
    rationale = None
    if comment is not None:
        rationale = comment["text"] or None
        if comment["reason"]:
            metadata["reason"] = comment["reason"]
    value = row["action"] == "up"
    name = "answer_quality" if row["target"] == "answer" else "passage_relevance"
    # override_feedback returns a new assessment and invalidates the old one, so the
    # next override must target the new id.
    if assessment_id:
        assessment = mlflow.override_feedback(
            trace_id=trace_id,
            assessment_id=assessment_id,
            value=value,
            rationale=rationale,
            source=source,
            metadata=metadata or None,
        )
    else:
        assessment = mlflow.log_feedback(
            trace_id=trace_id,
            name=name,
            value=value,
            rationale=rationale,
            source=source,
            metadata=metadata or None,
        )
    _save_assessment(db_path, row, rank_key, assessment.assessment_id)
    _set_status(db_path, row["event_id"], "synced")


def _latest_comment(db_path: Path, row, rank: int):
    """The newest comment written for this exact vote, if any."""
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT reason, text FROM feedback_comments
            WHERE user_id = ? AND interaction_id = ? AND target = ?
              AND COALESCE(rank, -1) = ? AND vote = ? AND created_at >= ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (
                row["user_id"],
                row["interaction_id"],
                row["target"],
                rank,
                row["action"],
                row["created_at"],
            ),
        ).fetchone()


def _trace_exists(trace_id: str) -> bool:
    """True when MLflow has the trace. A transport error is raised so the event retries."""
    import mlflow

    return mlflow.get_trace(trace_id, silent=True) is not None


def _assessment_id(
    db_path: Path, user_id: str, interaction_id: str, target: str, rank: int
) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT assessment_id FROM feedback_mirrors
            WHERE user_id = ? AND interaction_id = ? AND target = ? AND rank = ?
            """,
            (user_id, interaction_id, target, rank),
        ).fetchone()
    if row is None:
        return None
    return row["assessment_id"]


def _save_assessment(db_path: Path, row, rank: int, assessment_id: str | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback_mirrors (user_id, interaction_id, target, rank, assessment_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id, interaction_id, target, rank)
            DO UPDATE SET assessment_id = excluded.assessment_id
            """,
            (row["user_id"], row["interaction_id"], row["target"], rank, assessment_id),
        )


def _set_status(db_path: Path, event_id: str, status: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE feedback_events SET sync_status = ? WHERE event_id = ?",
            (status, event_id),
        )


def _retry_later(db_path: Path, event_id: str, attempt_count: int) -> None:
    attempt = attempt_count + 1
    delay = min(60, 2 ** min(attempt, 6))
    moment = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE feedback_events
            SET attempt_count = ?, next_attempt_at = ?, sync_status = 'pending'
            WHERE event_id = ? AND sync_status = 'pending'
            """,
            (attempt, moment, event_id),
        )


def _deleted_ids(db_path: Path) -> set[str]:
    from deepfix_rag.documents import init_documents

    init_documents(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT doc_id FROM documents WHERE deleted_at IS NOT NULL").fetchall()
    return {row["doc_id"] for row in rows}


def _answer_downvotes(db_path: Path) -> list[dict[str, str | None]]:
    rows = []
    for row in _latest_events(db_path):
        if row["target"] != "answer" or row["action"] != "down":
            continue
        if is_holdout(normalize_query(row["query"])):
            continue
        rows.append(
            {
                "interaction_id": row["interaction_id"],
                "user_id": row["user_id"],
                "query": row["query"],
                "answer": row["answer"],
                "config_path": row["config_path"],
            }
        )
    return rows


def _log_export(
    dev_path: Path,
    downvotes_path: Path,
    *,
    dev_count: int,
    holdout_count: int,
    snapshot_id: str,
    holdout_ids_sha256: str,
) -> str | None:
    try:
        import mlflow

        mlflow.set_experiment(FEEDBACK_EXPERIMENT)
        with mlflow.start_run() as run:
            mlflow.log_params(
                {
                    "dev_count": dev_count,
                    "holdout_count": holdout_count,
                    "snapshot_id": snapshot_id,
                    "holdout_ids_sha256": holdout_ids_sha256,
                }
            )
            mlflow.log_artifact(str(dev_path))
            mlflow.log_artifact(str(downvotes_path))
            return run.info.run_id
    except Exception:
        LOGGER.exception("Could not log feedback export %s", snapshot_id)
        return None


def _experiment_url(name: str) -> str | None:
    try:
        import mlflow

        experiment = mlflow.get_experiment_by_name(name)
        if experiment is None:
            return None
        tracking = mlflow.get_tracking_uri().rstrip("/")
        return f"{tracking}/#/experiments/{experiment.experiment_id}"
    except Exception:
        LOGGER.debug("Could not build an MLflow URL for %s", name, exc_info=True)
        return None


def _rate(up: int, down: int) -> float | None:
    total = up + down
    if total == 0:
        return None
    return up / total


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

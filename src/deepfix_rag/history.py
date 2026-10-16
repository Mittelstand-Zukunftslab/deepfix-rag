"""Admin review of every search and ask, with the answer returned and the feedback given."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from deepfix_rag.documents import document_filenames
from deepfix_rag.feedback import CommentReason, comments_for, init_feedback
from deepfix_rag.interactions import get_interaction, init_interactions
from deepfix_rag.storage import connect


class FeedbackFilter(StrEnum):
    """Narrow the history by the feedback an interaction received."""

    ALL = "all"
    ANY = "any"
    NEGATIVE = "negative"
    COMMENTED = "commented"
    NONE = "none"


@dataclass(frozen=True)
class VoteCounts:
    """Current votes: a cleared vote no longer counts, a switched vote counts once."""

    up: int = 0
    down: int = 0


@dataclass(frozen=True)
class HistoryEntry:
    """One row of the history list."""

    interaction_id: str
    kind: str
    user_id: str
    user_email: str
    query: str
    answer: str | None
    error: str | None
    config_path: str
    created_at: str
    hit_count: int
    answer_votes: VoteCounts
    passage_votes: VoteCounts
    comment_count: int


@dataclass(frozen=True)
class HistoryPage:
    """A page of history, newest first."""

    entries: list[HistoryEntry]
    total: int


@dataclass(frozen=True)
class ReviewedComment:
    """A comment and the vote it was written for."""

    vote: str
    reason: CommentReason | None
    text: str
    created_at: str


@dataclass(frozen=True)
class ReviewedHit:
    """A passage as it was shown, with the feedback it received."""

    rank: int
    doc_id: str
    document_filename: str
    text: str
    score: float
    votes: VoteCounts
    comments: list[ReviewedComment]


@dataclass(frozen=True)
class InteractionReview:
    """One interaction in full."""

    entry: HistoryEntry
    build_id: str
    trace_id: str | None
    answer_comments: list[ReviewedComment]
    hits: list[ReviewedHit]


# Latest event per voter, target, and passage rank; "clear" means no vote.
_CURRENT_VOTES = """
    WITH latest AS (
        SELECT e.interaction_id, e.target, e.rank, e.action
        FROM feedback_events AS e
        WHERE e.rowid = (
            SELECT e2.rowid FROM feedback_events AS e2
            WHERE e2.user_id = e.user_id
              AND e2.interaction_id = e.interaction_id
              AND e2.target = e.target
              AND COALESCE(e2.rank, -1) = COALESCE(e.rank, -1)
            ORDER BY e2.created_at DESC, e2.rowid DESC
            LIMIT 1
        )
          AND e.action != 'clear'
    ),
    votes AS (
        SELECT interaction_id,
               SUM(target = 'answer' AND action = 'up') AS answer_up,
               SUM(target = 'answer' AND action = 'down') AS answer_down,
               SUM(target = 'passage' AND action = 'up') AS passage_up,
               SUM(target = 'passage' AND action = 'down') AS passage_down
        FROM latest
        GROUP BY interaction_id
    )
"""

_COUNTS = """
    (SELECT COUNT(*) FROM interaction_hits AS h
     WHERE h.interaction_id = i.interaction_id) AS hit_count,
    (SELECT COUNT(*) FROM feedback_comments AS c
     WHERE c.interaction_id = i.interaction_id) AS comment_count
"""

_FEEDBACK_WHERE = {
    FeedbackFilter.ALL: "1 = 1",
    FeedbackFilter.ANY: "v.interaction_id IS NOT NULL",
    FeedbackFilter.NEGATIVE: "(v.answer_down > 0 OR v.passage_down > 0)",
    FeedbackFilter.COMMENTED: (
        "EXISTS (SELECT 1 FROM feedback_comments AS c WHERE c.interaction_id = i.interaction_id)"
    ),
    FeedbackFilter.NONE: "v.interaction_id IS NULL",
}


def list_history(
    db_path: Path,
    *,
    limit: int = 25,
    offset: int = 0,
    kind: str | None = None,
    feedback: FeedbackFilter = FeedbackFilter.ALL,
    search: str | None = None,
) -> HistoryPage:
    """Return interactions from every user, newest first, filtered and paginated."""
    init_interactions(db_path)
    init_feedback(db_path)
    filters = [_FEEDBACK_WHERE[feedback]]
    params: list[Any] = []
    if kind:
        filters.append("i.kind = ?")
        params.append(kind)
    if search and search.strip():
        filters.append("(i.query LIKE ? ESCAPE '\\' OR i.user_email LIKE ? ESCAPE '\\')")
        pattern = f"%{_escape_like(search.strip())}%"
        params.extend([pattern, pattern])
    where = " AND ".join(filters)
    source = f"FROM interactions AS i LEFT JOIN votes AS v USING (interaction_id) WHERE {where}"
    with connect(db_path) as conn:
        total = conn.execute(f"{_CURRENT_VOTES} SELECT COUNT(*) AS n {source}", params).fetchone()[
            "n"
        ]
        rows = conn.execute(
            f"""
            {_CURRENT_VOTES}
            SELECT i.*, v.answer_up, v.answer_down, v.passage_up, v.passage_down,
                   {_COUNTS}
            {source}
            ORDER BY i.created_at DESC, i.rowid DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return HistoryPage(entries=[_entry(row) for row in rows], total=total)


def review_interaction(db_path: Path, interaction_id: str) -> InteractionReview | None:
    """Return one interaction with every passage shown and the votes each received."""
    stored = get_interaction(db_path, interaction_id)
    if stored is None:
        return None
    init_feedback(db_path)
    with connect(db_path) as conn:
        summary = conn.execute(
            f"""
            {_CURRENT_VOTES}
            SELECT i.*, v.answer_up, v.answer_down, v.passage_up, v.passage_down,
                   {_COUNTS}
            FROM interactions AS i LEFT JOIN votes AS v USING (interaction_id)
            WHERE i.interaction_id = ?
            """,
            (interaction_id,),
        ).fetchone()
        by_rank = {
            int(row["rank"]): VoteCounts(up=int(row["up"]), down=int(row["down"]))
            for row in conn.execute(
                f"""
                {_CURRENT_VOTES}
                SELECT rank, SUM(action = 'up') AS up, SUM(action = 'down') AS down
                FROM latest
                WHERE interaction_id = ? AND target = 'passage' AND rank IS NOT NULL
                GROUP BY rank
                """,
                (interaction_id,),
            )
        }
    names = document_filenames(db_path, [hit.doc_id for hit in stored.hits])
    comments: dict[int | None, list[ReviewedComment]] = {}
    for comment in comments_for(db_path, interaction_id):
        comments.setdefault(comment.rank, []).append(
            ReviewedComment(
                vote=comment.vote,
                reason=comment.reason,
                text=comment.text,
                created_at=comment.created_at,
            )
        )
    return InteractionReview(
        entry=_entry(summary),
        build_id=stored.build_id,
        trace_id=stored.trace_id,
        answer_comments=comments.get(None, []),
        hits=[
            ReviewedHit(
                rank=hit.rank,
                doc_id=hit.doc_id,
                document_filename=names.get(hit.doc_id, hit.doc_id),
                text=hit.text,
                score=hit.score,
                votes=by_rank.get(hit.rank, VoteCounts()),
                comments=comments.get(hit.rank, []),
            )
            for hit in stored.hits
        ],
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _entry(row: Any) -> HistoryEntry:
    return HistoryEntry(
        interaction_id=row["interaction_id"],
        kind=row["kind"],
        user_id=row["user_id"],
        user_email=row["user_email"],
        query=row["query"],
        answer=row["answer"],
        error=row["error"],
        config_path=row["config_path"],
        created_at=row["created_at"],
        hit_count=int(row["hit_count"]),
        answer_votes=VoteCounts(up=int(row["answer_up"] or 0), down=int(row["answer_down"] or 0)),
        passage_votes=VoteCounts(
            up=int(row["passage_up"] or 0), down=int(row["passage_down"] or 0)
        ),
        comment_count=int(row["comment_count"]),
    )

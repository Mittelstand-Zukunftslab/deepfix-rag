"""The Ask chat store: a member's threads, the turns inside them, and their schema.

One module owns the chat's SQLite schema and every read and write against it, so
the HTTP routes stay thin adaptors. It uses the same file as ``admin_chat`` and
``interactions`` (``settings.product_db_path``) through the shared
``deepfix_rag.storage.connect`` context manager.

Three rules shape it, and each has exactly one implementation here:

* **A thread is private to its owner** (D-5). Every read and every write takes
  ``owner_id`` and filters on it, and another member's thread or turn is a
  ``KeyError`` so the route answers 404 rather than 403: a 403 would confirm that
  the id exists.
* **A delete keeps the evidence** (D-9). Deleting a thread removes its turns and
  then the thread row, children before parent, in one transaction. The
  ``interactions`` rows those turns produced, their ``interaction_hits``, and
  every ``feedback_events``, ``feedback_mirrors`` and ``feedback_comments`` row
  survive, because they are the optimizer's dataset: an export that shrank when
  a member tidied their history would let a member change what the gate scores.
* **Threads are unbounded and the list has a display cap** (D-11). ``LIST_LIMIT``
  is how many rows ``list_threads`` returns, not a lifecycle rule. There is
  deliberately no prune here, no age setting, and no delete-by-age: an older
  thread is still listed when it is within the cap and always still loadable by
  id.

``ask_chat_turns`` holds turn state only — the columns D-5 lists — and the
question and answer text stay in the ``interactions`` row the turn links to
through ``interactions.turn_id``. That keeps one row per assistant turn holding
the answer, which is what votes, history, the MLflow mirror, and the optimizer's
export already read (FR-019).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

from deepfix_rag.documents import document_filenames
from deepfix_rag.grounding import Grounding
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.interactions import init_interactions, record_interaction
from deepfix_rag.interactions import link_interaction as link_stored_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.service import INDEX_PREPARING, displayed_limit
from deepfix_rag.storage import connect

INTERRUPTED = "Interrupted: the API restarted while this answer was in progress."
TURN_STATUSES: tuple[str, ...] = ("running", "completed", "failed", "cancelled")
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "failed", "cancelled")
TITLE_MAX_CHARS = 120
LIST_LIMIT = 50
_WHITESPACE = re.compile(r"\s+")

TOOL_NAME = "search_documents"
TOOL_QUERY_MAX_CHARS = 500

# The one declaration of the tool's contract (D-2). The OpenAI-style definition
# the model receives is built from this object at import time, so the schema the
# provider validates against and the schema the tests and the loop read cannot be
# two different things (AC 1).
SEARCH_DOCUMENTS_SCHEMA: dict[str, object] = {
    "name": TOOL_NAME,
    "description": (
        "Search the DeepFix document corpus. Returns the ranked passages that survived the "
        "relevance floor, with their document and score. Call it again with a better query "
        "when the passages do not answer the question."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": TOOL_QUERY_MAX_CHARS,
                "description": "The standalone search query, in the corpus language.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "How many passages to return. Clamped to the window the product itself "
                    "shows; omit it for all of them."
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

SEARCH_DOCUMENTS_TOOL: dict[str, object] = {
    "type": "function",
    "function": SEARCH_DOCUMENTS_SCHEMA,
}

# The two reads a thread load makes, as constants so a test can assert the query
# plan of the SQL the module actually issues rather than a copy of it (AC 4).
#
# The turns read joins the ``interactions`` row that holds the question and the
# answer. One assistant turn writes exactly one such row (FR-019), which is what
# keeps the join one-to-one; a second row for the same turn would be a defect in
# that writer rather than a case this read has to resolve.
THREADS_BY_OWNER_SQL = (
    "SELECT thread_id, title, owner_id, owner_email, created_at, updated_at "
    "FROM ask_chat_threads WHERE owner_id = ? ORDER BY updated_at DESC LIMIT ?"
)
TURNS_BY_THREAD_SQL = (
    "SELECT t.turn_id, t.thread_id, t.owner_id, t.seq, t.status, t.retry_of, t.error, "
    "t.grounding, t.citations, t.retrieval_query, t.first_token_ms, t.started_at, "
    "t.ended_at, i.interaction_id, i.query AS question, i.answer "
    "FROM ask_chat_turns AS t "
    "LEFT JOIN interactions AS i ON i.turn_id = t.turn_id "
    "WHERE t.thread_id = ? AND t.owner_id = ? ORDER BY t.seq"
)

_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS ask_chat_threads (
        thread_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        owner_id TEXT NOT NULL,
        owner_email TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ask_chat_turns (
        turn_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(
            status IN ({", ".join(f"'{value}'" for value in TURN_STATUSES)})
        ),
        retry_of TEXT,
        error TEXT,
        grounding TEXT CHECK(
            grounding IS NULL OR grounding IN (
                {", ".join(f"'{value.value}'" for value in Grounding)}
            )
        ),
        citations TEXT NOT NULL DEFAULT '[]',
        retrieval_query TEXT,
        first_token_ms INTEGER,
        started_at TEXT NOT NULL,
        ended_at TEXT
    );
"""

# One index per load query, plus the owner lookup the rate limit counts on.
_INDEXES = """
    CREATE INDEX IF NOT EXISTS ask_chat_threads_by_owner
        ON ask_chat_threads (owner_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS ask_chat_turns_by_thread
        ON ask_chat_turns (thread_id, seq);
    CREATE INDEX IF NOT EXISTS ask_chat_turns_by_owner
        ON ask_chat_turns (owner_id, started_at);
"""


@dataclass(frozen=True)
class Thread:
    """One conversation, owned by the member who created it."""

    thread_id: str
    title: str
    owner_id: str
    owner_email: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Citation:
    """One ``[n]`` marker and the passage it resolved to."""

    marker: int
    rank: int
    doc_id: str


@dataclass(frozen=True)
class Turn:
    """One question and the answer stored for it.

    ``interaction_id``, ``question`` and ``answer`` come from the ``interactions``
    row the turn links to and are ``None`` until one is written, which is what a
    turn that is still ``running`` looks like. ASKCHAT-003 writes that row.
    """

    turn_id: str
    thread_id: str
    owner_id: str
    seq: int
    status: str
    retry_of: str | None
    error: str | None
    grounding: str | None
    citations: list[Citation]
    retrieval_query: str | None
    first_token_ms: int | None
    started_at: str
    ended_at: str | None
    interaction_id: str | None
    question: str | None
    answer: str | None


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk, as the model and the sources panel see it."""

    rank: int
    doc_id: str
    filename: str
    text: str
    score: float
    truncated: bool

    def as_dict(self) -> dict[str, object]:
        """The tool-output shape. The full text travels; ``text`` is the bounded one."""
        return {
            "rank": self.rank,
            "doc_id": self.doc_id,
            "filename": self.filename,
            "text": self.text,
            "score": self.score,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class SearchResult:
    """What ``search_documents`` returns, and what a turn stores as its hits."""

    query: str
    grounding: str
    passages: list[Passage]
    max_score: float
    build_id: str | None
    results: list[RetrievalResult]

    @property
    def empty(self) -> bool:
        return not self.passages

    def as_dict(self) -> dict[str, object]:
        """The D-2 output object, which is also the stream's tool output."""
        return {
            "query": self.query,
            "grounding": self.grounding,
            "passages": [passage.as_dict() for passage in self.passages],
            "max_score": self.max_score,
        }


class ToolInputError(ValueError):
    """The arguments fail the declared input schema, so nothing is retrieved."""


class IndexNotReadyError(RuntimeError):
    """No serving index, so the tool cannot search. The route maps this to 503."""


def displayed_floor(coordinator: IndexCoordinator) -> float:
    """The ``rerank.min_relevance_score`` the serving service is applying right now.

    Reported to the client so a turn's evidence can be read against the floor that
    produced it (D-3). This is a report, not a filter: the comparison happens once,
    inside the retrieval path the tool calls.
    """
    lease = coordinator.checkout()
    if lease is None:
        return 0.0
    try:
        return float(lease.service.config.rerank.min_relevance_score)
    finally:
        coordinator.release(lease)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def init_ask_chat(db_path: Path) -> None:
    """Create the chat tables and their indexes. Additive and idempotent.

    A second run adds and drops nothing. ``init_interactions`` runs first so the
    two columns a turn links through exist even on a database no search has
    touched yet; that function owns the ``interactions`` table and its migration,
    so the columns are declared in one place.
    """
    init_interactions(db_path)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_INDEXES)


def title_from(message: str) -> str:
    """A thread title from its first question: one line, at most 120 characters."""
    return _WHITESPACE.sub(" ", message).strip()[:TITLE_MAX_CHARS].strip()


def create_thread(
    db_path: Path,
    owner_id: str,
    *,
    owner_email: str = "",
    title: str | None = None,
) -> Thread:
    """Open one thread owned by ``owner_id``. Its title is derived when it is not given."""
    init_ask_chat(db_path)
    thread_id = _new_id()
    at = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ask_chat_threads
                (thread_id, title, owner_id, owner_email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (thread_id, title_from(title or ""), owner_id, owner_email, at, at),
        )
    return get_thread(db_path, thread_id, owner_id)


def list_threads(db_path: Path, owner_id: str, limit: int = LIST_LIMIT) -> list[Thread]:
    """This member's threads, newest first, at most ``limit`` of them.

    The cap is display-only (D-11): a thread past it still exists and ``get_thread``
    still loads it.
    """
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(THREADS_BY_OWNER_SQL, (owner_id, limit)).fetchall()
    return [_thread_from_row(row) for row in rows]


def get_thread(db_path: Path, thread_id: str, owner_id: str) -> Thread:
    """One of this member's threads. Another member's id is a ``KeyError``, so routes 404."""
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ask_chat_threads WHERE thread_id = ? AND owner_id = ?",
            (thread_id, owner_id),
        ).fetchone()
    if row is None:
        raise KeyError(thread_id)
    return _thread_from_row(row)


def rename_thread(db_path: Path, thread_id: str, owner_id: str, title: str) -> Thread:
    """Set a thread's title. One to 120 characters; anything else is refused, unchanged."""
    cleaned = _checked_title(title)
    get_thread(db_path, thread_id, owner_id)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE ask_chat_threads SET title = ? WHERE thread_id = ? AND owner_id = ?",
            (cleaned, thread_id, owner_id),
        )
    return get_thread(db_path, thread_id, owner_id)


def reopen_turn(
    db_path: Path,
    turn_id: str,
    owner_id: str,
    *,
    question: str,
) -> Turn:
    """Reopen a settled turn in place for a regenerate (FR-015).

    The turn keeps its ``turn_id`` and ``seq``, so the thread does not grow a
    second answer beside the first, and its ``ended_at`` is cleared with the
    status because the turn is running again. Its ``interactions`` row is
    rewritten by the caller, which is how the previous answer's text and votes go.
    """
    turn = get_turn(db_path, turn_id, owner_id)
    if turn.status == "running":
        raise TurnBusy(turn_id)
    if not turn.interaction_id:
        raise KeyError(turn_id)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE ask_chat_turns
            SET status = 'running', error = NULL, grounding = NULL, citations = '[]',
                retrieval_query = NULL, first_token_ms = NULL, started_at = ?, ended_at = NULL
            WHERE turn_id = ? AND owner_id = ?
            """,
            (_now(), turn_id, owner_id),
        )
        _touch_thread(conn, turn.thread_id, _now(), question)
    return get_turn(db_path, turn_id, owner_id)


def clear_turn_votes(db_path: Path, turn: Turn) -> int:
    """Clear every vote on a turn's answer and passages before its text is replaced.

    It goes through ``feedback.record_vote`` with the existing ``clear`` action,
    which appends an event and deletes the mirrored MLflow assessment, so no
    assessment points at text nobody can see (FR-015). No new vote action and no
    ``CHECK`` change.
    """
    from deepfix_rag.feedback import current_votes, record_vote

    if not turn.interaction_id:
        return 0
    answer_vote, passage_votes = current_votes(db_path, turn.owner_id, turn.interaction_id)
    cleared = 0
    if answer_vote is not None:
        record_vote(
            db_path,
            user_id=turn.owner_id,
            interaction_id=turn.interaction_id,
            target="answer",
            action="clear",
        )
        cleared += 1
    for rank in sorted(passage_votes):
        record_vote(
            db_path,
            user_id=turn.owner_id,
            interaction_id=turn.interaction_id,
            target="passage",
            rank=rank,
            action="clear",
        )
        cleared += 1
    return cleared


class RateRefusal:
    """The limiter's refusal: what to tell the member, and how long to wait."""

    def __init__(self, message: str, retry_after: int) -> None:
        self.message = message
        self.retry_after = retry_after


BUSY_MESSAGE = "The assistant is busy on this server. Try again in a moment."


def check_rate_limit(
    db_path: Path,
    owner_id: str,
    *,
    max_turns: int,
    window_seconds: float,
    max_concurrent: int,
    now: datetime | None = None,
) -> RateRefusal | None:
    """A member's turn budget, then the deployment's concurrent-turn cap (D-8).

    The count reads ``ask_chat_turns`` only, so a refusal is not itself a turn and
    cannot ratchet into a longer ban: refusing writes nothing. ``now`` is
    injectable so a test never sleeps out a window, and this mirrors
    ``admin_chat.check_rate_limit`` rather than inventing a second limiter.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    since = (moment - timedelta(seconds=window_seconds)).isoformat()
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        recent = conn.execute(
            """
            SELECT started_at FROM ask_chat_turns
            WHERE owner_id = ? AND started_at >= ? ORDER BY started_at
            """,
            (owner_id, since),
        ).fetchall()
        if len(recent) >= max_turns:
            oldest = _parse_time(str(recent[0]["started_at"]))
            wait = max(
                1, int((oldest + timedelta(seconds=window_seconds) - moment).total_seconds())
            )
            return RateRefusal(
                message=(
                    f"You have used {len(recent)} of {max_turns} questions in this window. "
                    f"Try again in {wait} seconds."
                ),
                retry_after=wait,
            )
        concurrent = conn.execute(
            "SELECT COUNT(*) FROM ask_chat_turns WHERE status = 'running'"
        ).fetchone()[0]
    if concurrent >= max_concurrent:
        return RateRefusal(message=BUSY_MESSAGE, retry_after=5)
    return None


def _parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


class TurnBusy(RuntimeError):
    """The turn is still being written, so a regenerate must not replace it (FR-015)."""

    def __init__(self, turn_id: str) -> None:
        super().__init__("That answer is still being written.")
        self.turn_id = turn_id


def _checked_title(title: str) -> str:
    """The trimmed title, or a ``ValueError`` when it is empty or longer than 120."""
    cleaned = _WHITESPACE.sub(" ", title).strip()
    if not 1 <= len(cleaned) <= TITLE_MAX_CHARS:
        raise ValueError(f"A thread title must be 1 to {TITLE_MAX_CHARS} characters.")
    return cleaned


def delete_thread(db_path: Path, thread_id: str, owner_id: str) -> bool:
    """Hard-delete one thread, its turns first, in one transaction (D-9).

    Returns ``True`` when this call removed it and ``False`` when there was
    nothing of this member's to remove, which the route answers 404 either way
    so another member's id does not leak. The turn's ``interactions`` row, its
    hits, and its votes are deliberately untouched.
    """
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT 1 FROM ask_chat_threads WHERE thread_id = ? AND owner_id = ?",
            (thread_id, owner_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "DELETE FROM ask_chat_turns WHERE thread_id = ? AND owner_id = ?",
            (thread_id, owner_id),
        )
        conn.execute(
            "DELETE FROM ask_chat_threads WHERE thread_id = ? AND owner_id = ?",
            (thread_id, owner_id),
        )
    return True


def append_turn(
    db_path: Path,
    thread_id: str,
    owner_id: str,
    *,
    question: str = "",
    status: str = "completed",
    grounding: Grounding | str | None = None,
    citations: list[Citation] | None = None,
    retrieval_query: str | None = None,
    error: str | None = None,
    retry_of: str | None = None,
    first_token_ms: int | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> Turn:
    """Append one turn at the thread's next ``seq``.

    This writes a whole turn in one call, which is what a caller that already
    holds the answer uses — the import does. A streaming turn opens with
    ``open_turn`` and settles with ``finish_turn``.
    """
    if status not in TURN_STATUSES:
        raise ValueError(f"unknown turn status: {status!r}")
    get_thread(db_path, thread_id, owner_id)
    if retry_of is not None:
        _assert_turn_in_thread(db_path, retry_of, thread_id, owner_id)
    at = _now()
    turn_id = _new_id()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO ask_chat_turns (
                turn_id, thread_id, owner_id, seq, status, retry_of, error, grounding,
                citations, retrieval_query, first_token_ms, started_at, ended_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                thread_id,
                owner_id,
                _next_seq(conn, thread_id),
                status,
                retry_of,
                error,
                _grounding_value(grounding),
                _dump_citations(citations or []),
                retrieval_query,
                first_token_ms,
                started_at or at,
                # A turn opened `running` has not ended; any other status has.
                ended_at if ended_at is not None else (None if status == "running" else at),
            ),
        )
        _touch_thread(conn, thread_id, at, question)
    return get_turn(db_path, turn_id, owner_id)


def open_turn(
    db_path: Path,
    thread_id: str,
    owner_id: str,
    *,
    question: str = "",
    retry_of: str | None = None,
) -> Turn:
    """Open one turn in ``running`` at the thread's next ``seq``."""
    return append_turn(
        db_path,
        thread_id,
        owner_id,
        question=question,
        status="running",
        retry_of=retry_of,
    )


def finish_turn(
    db_path: Path,
    turn_id: str,
    owner_id: str,
    *,
    status: str,
    error: str | None = None,
    grounding: Grounding | str | None = None,
    citations: list[Citation] | None = None,
    retrieval_query: str | None = None,
    first_token_ms: int | None = None,
) -> Turn:
    """Settle a turn with one of the three terminal statuses and stamp ``ended_at``.

    ``error`` is stored exactly as given, ``None`` included, so settling a turn
    without a reason clears any earlier one. Redacting the sentence is the
    caller's job, because only the caller knows which values are private.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"not a terminal turn status: {status!r}")
    get_turn(db_path, turn_id, owner_id)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE ask_chat_turns
            SET status = ?,
                error = ?,
                grounding = COALESCE(?, grounding),
                citations = COALESCE(?, citations),
                retrieval_query = COALESCE(?, retrieval_query),
                first_token_ms = COALESCE(?, first_token_ms),
                ended_at = ?
            WHERE turn_id = ? AND owner_id = ?
            """,
            (
                status,
                error,
                _grounding_value(grounding),
                None if citations is None else _dump_citations(citations),
                retrieval_query,
                first_token_ms,
                _now(),
                turn_id,
                owner_id,
            ),
        )
    return get_turn(db_path, turn_id, owner_id)


def set_grounding(
    db_path: Path,
    turn_id: str,
    owner_id: str,
    grounding: Grounding | str,
    citations: list[Citation] | None = None,
) -> Turn:
    """Record the grounding verdict and its citations without touching the turn's status."""
    get_turn(db_path, turn_id, owner_id)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE ask_chat_turns
            SET grounding = ?, citations = COALESCE(?, citations)
            WHERE turn_id = ? AND owner_id = ?
            """,
            (
                _grounding_value(grounding),
                None if citations is None else _dump_citations(citations),
                turn_id,
                owner_id,
            ),
        )
    return get_turn(db_path, turn_id, owner_id)


def get_turn(db_path: Path, turn_id: str, owner_id: str) -> Turn:
    """One of this member's turns. Another member's id is a ``KeyError``, so routes 404."""
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT t.turn_id, t.thread_id, t.owner_id, t.seq, t.status, t.retry_of,
                   t.error, t.grounding, t.citations, t.retrieval_query, t.first_token_ms,
                   t.started_at, t.ended_at, i.interaction_id, i.query AS question, i.answer
            FROM ask_chat_turns AS t
            LEFT JOIN interactions AS i ON i.turn_id = t.turn_id
            WHERE t.turn_id = ? AND t.owner_id = ?
            """,
            (turn_id, owner_id),
        ).fetchone()
    if row is None:
        raise KeyError(turn_id)
    return _turn_from_row(row)


def thread_turns(db_path: Path, thread_id: str, owner_id: str) -> list[Turn]:
    """The thread's turns in ``seq`` order, after the ownership check."""
    get_thread(db_path, thread_id, owner_id)
    with connect(db_path) as conn:
        rows = conn.execute(TURNS_BY_THREAD_SQL, (thread_id, owner_id)).fetchall()
    return [_turn_from_row(row) for row in rows]


def open_turn_interaction(
    db_path: Path,
    turn: Turn,
    *,
    question: str,
    config_path: str,
    user_email: str = "",
) -> str:
    """Insert the turn's single ``interactions`` row before its answer exists (FR-019).

    Opening the row before the first byte is what lets a cancelled or failed turn
    keep its question and the text the member had read (D-4); the answer and the
    hits are filled in later by ``interactions.replace_interaction``. ``kind`` is
    ``'ask'`` and the table's ``CHECK`` is untouched, so votes, history, the MLflow
    assessment mirror, and the optimizer's export keep working with one shape.
    """
    return record_interaction(
        db_path,
        kind="ask",
        user_id=turn.owner_id,
        user_email=user_email,
        query=question,
        build_id="",
        config_path=config_path,
        results=[],
        answer=None,
    ).interaction_id


def link_interaction(
    db_path: Path,
    turn_id: str,
    owner_id: str,
    interaction_id: str,
) -> None:
    """Point an ``interactions`` row at the turn it answers, so a load can find its text.

    The write itself belongs to ``interactions.link_interaction``, which owns that
    table and its ownership check; this resolves the thread the turn belongs to
    and refuses a turn that is not the caller's own.
    """
    turn = get_turn(db_path, turn_id, owner_id)
    link_stored_interaction(
        db_path,
        interaction_id,
        user_id=owner_id,
        thread_id=turn.thread_id,
        turn_id=turn_id,
    )


def count_recent_turns(db_path: Path, owner_id: str, window_seconds: float) -> int:
    """Turns this member started inside the window, counted from ``ask_chat_turns``."""
    init_ask_chat(db_path)
    since = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    with connect(db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM ask_chat_turns WHERE owner_id = ? AND started_at >= ?",
                (owner_id, since),
            ).fetchone()[0]
        )


def fail_interrupted_ask_turns(db_path: Path) -> int:
    """Fail every turn a restart left ``running``. Called from the API lifespan (NFR-1)."""
    init_ask_chat(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE ask_chat_turns SET status = 'failed', error = ?, ended_at = ?
            WHERE status = 'running'
            """,
            (INTERRUPTED, _now()),
        )
        return int(cursor.rowcount or 0)


async def search_documents(
    coordinator: IndexCoordinator,
    db_path: Path,
    arguments: dict[str, object],
    *,
    max_passage_chars: int,
    max_displayed_hits: int,
) -> SearchResult:
    """Run the one tool: the passages that survived the relevance floor.

    It reports what ``QueryService.retrieve`` returned. The floor lives inside
    that call (``_search_split`` → ``RAGWorkflow._ranker_split``), so nothing here
    compares a score against ``rerank.min_relevance_score`` a second time: a
    threshold of the tool's own would be a second contract that could drift from
    the one the optimizer tunes (AC 4, D-6).

    The one thing this adds is depth. ``QueryService.retrieve`` returns its whole
    ranked window because ``displayed_limit`` is applied at the route; the tool
    applies the same helper, so the model never receives more passages than the
    Search page would show (AC 6, AC 7).
    """
    query = _tool_query(arguments)
    lease = coordinator.checkout()
    if lease is None:
        raise IndexNotReadyError(INDEX_PREPARING)
    try:
        found = await lease.service.retrieve(query)
        limit = displayed_limit(lease.service.config, max_displayed_hits)
    finally:
        # A raised error must not pin a build that is being replaced (AC 3).
        coordinator.release(lease)
    top_k = _clamped_top_k(arguments.get("top_k"), limit)
    results = found[: min(limit, top_k)]
    if not results:
        return SearchResult(
            query=query,
            grounding=Grounding.NO_RELEVANT_SOURCES.value,
            passages=[],
            max_score=0.0,
            build_id=lease.build_id,
            results=[],
        )
    names = document_filenames(db_path, [result.doc_id for result in results])
    passages = [
        Passage(
            rank=rank,
            doc_id=result.doc_id,
            filename=names.get(result.doc_id, result.doc_id),
            text=result.text[:max_passage_chars],
            score=float(result.score or 0.0),
            truncated=len(result.text) > max_passage_chars,
        )
        for rank, result in enumerate(results, start=1)
    ]
    return SearchResult(
        query=query,
        grounding=Grounding.GROUNDED.value,
        passages=passages,
        max_score=max(float(result.score or 0.0) for result in results),
        build_id=lease.build_id,
        results=results,
    )


def _tool_query(arguments: dict[str, object]) -> str:
    """The searched query, or ``ToolInputError`` when it fails the declared schema."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolInputError("search_documents needs a non-empty query.")
    if len(query) > TOOL_QUERY_MAX_CHARS:
        raise ToolInputError(f"A search query must be at most {TOOL_QUERY_MAX_CHARS} characters.")
    return query.strip()


def _clamped_top_k(requested: object, limit: int) -> int:
    """A requested depth inside ``[1, limit]``. Out of range is clamped, not refused (AC 7)."""
    if isinstance(requested, bool) or not isinstance(requested, int):
        return limit
    return max(1, min(requested, limit))


def _next_seq(conn: sqlite3.Connection, thread_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM ask_chat_turns WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
    )


def _touch_thread(
    conn: sqlite3.Connection,
    thread_id: str,
    at: str,
    question: str,
) -> None:
    """Move the thread's ``updated_at`` forward, and name it from its first question."""
    conn.execute(
        "UPDATE ask_chat_threads SET updated_at = ? WHERE thread_id = ?",
        (at, thread_id),
    )
    if question:
        conn.execute(
            "UPDATE ask_chat_threads SET title = ? WHERE thread_id = ? AND TRIM(title) = ''",
            (title_from(question), thread_id),
        )


def _assert_turn_in_thread(db_path: Path, turn_id: str, thread_id: str, owner_id: str) -> None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT thread_id FROM ask_chat_turns WHERE turn_id = ? AND owner_id = ?",
            (turn_id, owner_id),
        ).fetchone()
    if row is None or str(row["thread_id"]) != thread_id:
        raise ValueError("The turn being retried is not in this thread.")


def _grounding_value(grounding: Grounding | str | None) -> str | None:
    return None if grounding is None else str(getattr(grounding, "value", grounding))


def _dump_citations(citations: list[Citation]) -> str:
    return json.dumps(
        [
            {"marker": citation.marker, "rank": citation.rank, "doc_id": citation.doc_id}
            for citation in citations
        ],
        ensure_ascii=False,
    )


def _load_citations(text: str) -> list[Citation]:
    """Parse stored citations, dropping an entry the shape does not fit.

    A malformed row must not blank a thread: the badges lose one marker rather
    than the request failing.
    """
    try:
        parsed = json.loads(text or "[]")
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    citations: list[Citation] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            citations.append(
                Citation(
                    marker=int(item["marker"]),
                    rank=int(item["rank"]),
                    doc_id=str(item["doc_id"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return citations


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        thread_id=str(row["thread_id"]),
        title=str(row["title"]),
        owner_id=str(row["owner_id"]),
        owner_email=str(row["owner_email"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _turn_from_row(row: sqlite3.Row) -> Turn:
    return Turn(
        turn_id=str(row["turn_id"]),
        thread_id=str(row["thread_id"]),
        owner_id=str(row["owner_id"]),
        seq=int(row["seq"]),
        status=str(row["status"]),
        retry_of=None if row["retry_of"] is None else str(row["retry_of"]),
        error=None if row["error"] is None else str(row["error"]),
        grounding=None if row["grounding"] is None else str(row["grounding"]),
        citations=_load_citations(str(row["citations"] or "[]")),
        retrieval_query=(None if row["retrieval_query"] is None else str(row["retrieval_query"])),
        first_token_ms=None if row["first_token_ms"] is None else int(row["first_token_ms"]),
        started_at=str(row["started_at"]),
        ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
        interaction_id=(None if row["interaction_id"] is None else str(row["interaction_id"])),
        question=None if row["question"] is None else str(row["question"]),
        answer=None if row["answer"] is None else str(row["answer"]),
    )

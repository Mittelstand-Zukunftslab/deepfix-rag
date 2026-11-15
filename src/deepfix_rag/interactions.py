"""Stored searches and answers, including the passages that were shown."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid

from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect

InteractionKind = str


@dataclass(frozen=True)
class StoredHit:
    """One passage captured at the moment it was shown."""

    rank: int
    doc_id: str
    text: str
    passage_key: str
    score: float


@dataclass(frozen=True)
class Interaction:
    """A search or a single-turn question."""

    interaction_id: str
    kind: str
    user_id: str
    user_email: str
    query: str
    build_id: str
    config_path: str
    answer: str | None
    error: str | None
    trace_id: str | None
    created_at: str
    hits: list[StoredHit]


def init_interactions(db_path: Path) -> None:
    """Create interaction tables if they do not exist.

    ``thread_id`` and ``turn_id`` are the two nullable columns an Ask chat turn
    fills in. They are added the way ``user_email`` was — a ``PRAGMA table_info``
    check then ``ALTER TABLE … ADD COLUMN`` — because the ``kind`` CHECK cannot
    be altered in place and a table rebuild is exactly what D-5 avoids.
    """
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('retrieval', 'ask')),
                user_id TEXT NOT NULL,
                query TEXT NOT NULL,
                build_id TEXT NOT NULL,
                config_path TEXT NOT NULL,
                answer TEXT,
                error TEXT,
                trace_id TEXT,
                created_at TEXT NOT NULL,
                user_email TEXT NOT NULL DEFAULT '',
                thread_id TEXT,
                turn_id TEXT
            )
            """
        )
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(interactions)")}
        if "user_email" not in columns:
            conn.execute("ALTER TABLE interactions ADD COLUMN user_email TEXT NOT NULL DEFAULT ''")
        for column in ("thread_id", "turn_id"):
            if column not in columns:
                conn.execute(f"ALTER TABLE interactions ADD COLUMN {column} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS interactions_by_turn ON interactions (turn_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interaction_hits (
                interaction_id TEXT NOT NULL REFERENCES interactions(interaction_id),
                rank INTEGER NOT NULL,
                doc_id TEXT NOT NULL,
                text TEXT NOT NULL,
                passage_key TEXT NOT NULL,
                score REAL NOT NULL,
                PRIMARY KEY (interaction_id, rank)
            )
            """
        )


def passage_key(text: str) -> str:
    """Identity of a passage: SHA-256 of the exact chunk text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_interaction(
    db_path: Path,
    *,
    kind: str,
    user_id: str,
    query: str,
    build_id: str,
    config_path: str,
    results: list[RetrievalResult],
    user_email: str = "",
    answer: str | None = None,
    error: str | None = None,
    trace_id: str | None = None,
    interaction_id: str | None = None,
) -> Interaction:
    """Persist one interaction and a snapshot of every hit that was returned.

    A chat turn is linked to its thread through ``link_interaction`` after this
    returns, so this table has one owner for the two nullable columns a
    conversation fills in and one path that sets them.
    """
    init_interactions(db_path)
    stored_id = interaction_id or uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    hits = [
        StoredHit(
            rank=index,
            doc_id=result.doc_id,
            text=result.text,
            passage_key=passage_key(result.text),
            score=float(result.score or 0.0),
        )
        for index, result in enumerate(results, start=1)
    ]
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO interactions (
                interaction_id, kind, user_id, user_email, query, build_id, config_path,
                answer, error, trace_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored_id,
                kind,
                user_id,
                user_email,
                query,
                build_id,
                config_path,
                answer,
                error,
                trace_id,
                created_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO interaction_hits (interaction_id, rank, doc_id, text, passage_key, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (stored_id, hit.rank, hit.doc_id, hit.text, hit.passage_key, hit.score)
                for hit in hits
            ],
        )
    return Interaction(
        interaction_id=stored_id,
        kind=kind,
        user_id=user_id,
        user_email=user_email,
        query=query,
        build_id=build_id,
        config_path=config_path,
        answer=answer,
        error=error,
        trace_id=trace_id,
        created_at=created_at,
        hits=hits,
    )


def link_interaction(
    db_path: Path,
    interaction_id: str,
    *,
    user_id: str,
    thread_id: str,
    turn_id: str,
) -> None:
    """Point an existing interaction at the chat turn it answers.

    The row must already belong to ``user_id``: this is how an imported browser
    session is attached to the interaction it was recorded against, and the
    ownership check is what stops a member claiming somebody else's record.
    """
    init_interactions(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE interactions SET thread_id = ?, turn_id = ?
            WHERE interaction_id = ? AND user_id = ?
            """,
            (thread_id, turn_id, interaction_id, user_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(interaction_id)


def set_trace_id(db_path: Path, interaction_id: str, trace_id: str | None) -> None:
    """Attach the turn's MLflow trace to its interaction.

    A turn that produced no answer still has a trace — a failed or stopped turn is
    accounted for — and this is what makes it reachable from the stored row rather
    than only from the experiment.
    """
    if trace_id is None:
        return
    init_interactions(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE interactions SET trace_id = ? WHERE interaction_id = ?",
            (trace_id, interaction_id),
        )


def update_answer(db_path: Path, interaction_id: str, answer: str) -> None:
    """Replace a stored interaction's answer text as a reply streams or is replaced.

    A cancelled turn keeps what the member had read (D-4), and a regenerate
    overwrites the answer it replaces so the previous text is nowhere in the
    thread. One row holds the answer, so there is one place to update.
    """
    init_interactions(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE interactions SET answer = ? WHERE interaction_id = ?",
            (answer, interaction_id),
        )


def replace_interaction(
    db_path: Path,
    interaction_id: str,
    *,
    answer: str,
    results: list[RetrievalResult],
    build_id: str,
    trace_id: str | None,
) -> None:
    """Fill in a chat turn's answer and hit snapshot once its tool calls are done.

    The row is inserted before the first byte so a cancelled turn can be read; its
    answer and hits are then written here rather than by a second insert, so one
    assistant turn still has exactly one ``interactions`` row (FR-019). The hits
    are replaced wholesale, because a regenerate may have found different passages.
    """
    init_interactions(db_path)
    hits = [
        StoredHit(
            rank=index,
            doc_id=result.doc_id,
            text=result.text,
            passage_key=passage_key(result.text),
            score=float(result.score or 0.0),
        )
        for index, result in enumerate(results, start=1)
    ]
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE interactions SET answer = ?, build_id = ?, trace_id = ? "
            "WHERE interaction_id = ?",
            (answer, build_id, trace_id, interaction_id),
        )
        conn.execute("DELETE FROM interaction_hits WHERE interaction_id = ?", (interaction_id,))
        conn.executemany(
            """
            INSERT INTO interaction_hits (interaction_id, rank, doc_id, text, passage_key, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (interaction_id, hit.rank, hit.doc_id, hit.text, hit.passage_key, hit.score)
                for hit in hits
            ],
        )


def get_interaction(db_path: Path, interaction_id: str) -> Interaction | None:
    """Return one interaction and its hit snapshot."""
    init_interactions(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM interactions WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()
        if row is None:
            return None
        hit_rows = conn.execute(
            """
            SELECT rank, doc_id, text, passage_key, score
            FROM interaction_hits
            WHERE interaction_id = ?
            ORDER BY rank
            """,
            (interaction_id,),
        ).fetchall()
    return Interaction(
        interaction_id=row["interaction_id"],
        kind=row["kind"],
        user_id=row["user_id"],
        user_email=row["user_email"],
        query=row["query"],
        build_id=row["build_id"],
        config_path=row["config_path"],
        answer=row["answer"],
        error=row["error"],
        trace_id=row["trace_id"],
        created_at=row["created_at"],
        hits=[
            StoredHit(
                rank=hit["rank"],
                doc_id=hit["doc_id"],
                text=hit["text"],
                passage_key=hit["passage_key"],
                score=float(hit["score"]),
            )
            for hit in hit_rows
        ],
    )

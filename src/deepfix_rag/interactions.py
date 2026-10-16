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
    """Create interaction tables if they do not exist."""
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
                user_email TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(interactions)")}
        if "user_email" not in columns:
            conn.execute("ALTER TABLE interactions ADD COLUMN user_email TEXT NOT NULL DEFAULT ''")
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
    """Persist one interaction and a snapshot of every hit that was returned."""
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

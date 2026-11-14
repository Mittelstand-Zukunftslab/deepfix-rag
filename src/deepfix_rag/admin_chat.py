"""The admin chat store: threads, turns, messages, action cards, and the audit trail.

One module owns the chat's SQLite schema and every read and write against it,
so the HTTP routes stay thin adaptors. It uses the same file as
``optimization_runs`` (``settings.product_db_path``) and the shared
``deepfix_rag.storage.connect`` context manager.

Two rules from the PRD shape the schema:

* **A thread delete is a hard delete, children before parent, in one
  transaction** (D-4): action cards, messages, turns, thread-run links, then the
  thread row. Audit rows survive it, deliberately, so the security record of who
  started a run outlives the conversation.
* **Audit rows are bounded by age, never by a delete** (D-6). ``prune_audit``
  takes the window as an argument, so no retention literal lives here and the
  API's scheduler decides when it runs.

``admin_chat_thread_runs.run_id`` carries no foreign key to
``optimization_runs``: a linked run is a pointer for display, and a pruned run
row must not cascade-delete a thread.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sqlite3
import uuid

from deepfix_rag import optimization
from deepfix_rag.storage import connect

INTERRUPTED = "Interrupted: the API restarted while this reply was in progress."
TITLE_CHARS = 60
_WHITESPACE = re.compile(r"\s+")

AUDIT_KINDS: tuple[str, ...] = (
    "turn_started",
    "turn_completed",
    "turn_cancelled",
    "turn_failed",
    "rate_limited",
    "action_proposed",
    "action_confirmed",
    "action_rejected",
    "action_expired",
    "thread_deleted",
)
TURN_STATUSES: tuple[str, ...] = ("running", "completed", "failed", "cancelled")
ACTION_STATUSES: tuple[str, ...] = ("proposed", "confirmed", "rejected", "expired", "failed")

# Messages by thread and threads by admin are the two reads a thread load makes.
# They are constants so the tests that assert an index search cannot drift from
# the SQL the module actually issues (NFR-3).
MESSAGES_BY_THREAD_SQL = (
    "SELECT message_id, thread_id, turn_id, role, seq, content, created_at "
    "FROM admin_chat_messages WHERE thread_id = ? ORDER BY seq"
)
THREADS_BY_ADMIN_SQL = (
    "SELECT thread_id, title, created_by, created_by_email, created_at, updated_at "
    "FROM admin_chat_threads WHERE created_by = ? ORDER BY updated_at DESC LIMIT ?"
)

_SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS admin_chat_threads (
        thread_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_by_email TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS admin_chat_turns (
        turn_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        admin_id TEXT NOT NULL,
        admin_email TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK(
            status IN ({", ".join(f"'{value}'" for value in TURN_STATUSES)})
        ),
        retry_of TEXT,
        error TEXT,
        request_chars INTEGER NOT NULL DEFAULT 0,
        first_token_ms INTEGER,
        started_at TEXT NOT NULL,
        ended_at TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_chat_messages (
        message_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        turn_id TEXT,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
        seq INTEGER NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS admin_chat_actions (
        action_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{{}}',
        status TEXT NOT NULL CHECK(
            status IN ({", ".join(f"'{value}'" for value in ACTION_STATUSES)})
        ),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        result TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_chat_thread_runs (
        thread_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        linked_at TEXT NOT NULL,
        PRIMARY KEY (thread_id, run_id)
    );
    CREATE TABLE IF NOT EXISTS admin_chat_audit (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        admin_id TEXT NOT NULL,
        admin_email TEXT NOT NULL DEFAULT '',
        thread_id TEXT,
        turn_id TEXT,
        kind TEXT NOT NULL CHECK(
            kind IN ({", ".join(f"'{value}'" for value in AUDIT_KINDS)})
        ),
        detail TEXT NOT NULL DEFAULT ''
    );
"""

_INDEXES = """
    CREATE INDEX IF NOT EXISTS admin_chat_threads_by_admin
        ON admin_chat_threads (created_by, updated_at DESC);
    CREATE INDEX IF NOT EXISTS admin_chat_messages_by_thread
        ON admin_chat_messages (thread_id, seq);
    CREATE INDEX IF NOT EXISTS admin_chat_turns_by_thread
        ON admin_chat_turns (thread_id, started_at);
    CREATE INDEX IF NOT EXISTS admin_chat_turns_by_admin
        ON admin_chat_turns (admin_id, started_at);
    CREATE INDEX IF NOT EXISTS admin_chat_actions_by_thread
        ON admin_chat_actions (thread_id, status);
"""


@dataclass(frozen=True)
class Thread:
    """One conversation, owned by the admin who created it."""

    thread_id: str
    title: str
    created_by: str
    created_by_email: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Message:
    """One stored user or assistant message."""

    message_id: str
    thread_id: str
    turn_id: str | None
    role: str
    seq: int
    content: str
    created_at: str


@dataclass(frozen=True)
class Turn:
    """One attempt at a reply."""

    turn_id: str
    thread_id: str
    admin_id: str
    admin_email: str
    status: str
    retry_of: str | None
    error: str | None
    request_chars: int
    first_token_ms: int | None
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class Action:
    """One proposed action card."""

    action_id: str
    thread_id: str
    turn_id: str
    kind: str
    payload: str
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str | None
    result: str | None


@dataclass(frozen=True)
class ThreadRun:
    """One optimization run linked to a thread."""

    thread_id: str
    run_id: str
    linked_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def init_admin_chat(db_path: Path) -> None:
    """Create the chat tables and their indexes. Additive and idempotent."""
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_INDEXES)


def redact(
    text: str,
    private_paths: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> str:
    """Remove configured credentials and private paths from ``text``.

    The credential list is ``optimization._SECRET_ENVS`` read from the process
    environment — one list for the whole repository — plus any value passed in,
    so a caller that already holds the key can redact it without mutating the
    environment. No second copy of the rule lives here.
    """
    found = [value for value in (os.environ.get(name, "") for name in optimization._SECRET_ENVS)]
    found.extend(secrets)
    found.extend(private_paths)
    values = tuple(value.strip() for value in found if isinstance(value, str) and len(value) >= 8)
    return optimization._redact(text, values)


def redacted_detail(
    text: str,
    private_paths: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> str:
    """An audit ``detail``: redacted, one line, and capped at the step preview."""
    cleaned = redact(
        _WHITESPACE.sub(" ", text).strip(), private_paths=private_paths, secrets=secrets
    )
    return cleaned[: optimization.STEP_PREVIEW_LIMIT]


def derive_title(content: str) -> str:
    """A thread title from its first user message. No model call, ever."""
    collapsed = _WHITESPACE.sub(" ", content).strip()
    return collapsed[:TITLE_CHARS].strip()


def create_thread(
    db_path: Path,
    admin_id: str,
    *,
    admin_email: str = "",
    title: str | None = None,
) -> Thread:
    """Create one thread owned by ``admin_id``."""
    init_admin_chat(db_path)
    thread_id = _new_id()
    at = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO admin_chat_threads
                (thread_id, title, created_by, created_by_email, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (thread_id, title or "", admin_id, admin_email, at, at),
        )
    return get_thread(db_path, thread_id, admin_id)


def list_threads(db_path: Path, admin_id: str, limit: int = 50) -> list[Thread]:
    """This admin's threads, newest first."""
    init_admin_chat(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(THREADS_BY_ADMIN_SQL, (admin_id, limit)).fetchall()
    return [_thread_from_row(row) for row in rows]


def get_thread(db_path: Path, thread_id: str, admin_id: str) -> Thread:
    """One of this admin's threads. Another admin's id is a ``KeyError``, so routes 404."""
    init_admin_chat(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM admin_chat_threads WHERE thread_id = ? AND created_by = ?",
            (thread_id, admin_id),
        ).fetchone()
    if row is None:
        raise KeyError(thread_id)
    return _thread_from_row(row)


def thread_messages(db_path: Path, thread_id: str, admin_id: str) -> list[Message]:
    """The thread's messages in ``seq`` order, after the ownership check."""
    get_thread(db_path, thread_id, admin_id)
    with connect(db_path) as conn:
        rows = conn.execute(MESSAGES_BY_THREAD_SQL, (thread_id,)).fetchall()
    return [_message_from_row(row) for row in rows]


def thread_turns(db_path: Path, thread_id: str, admin_id: str) -> list[Turn]:
    """The thread's turns, oldest first."""
    get_thread(db_path, thread_id, admin_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM admin_chat_turns WHERE thread_id = ? ORDER BY started_at, turn_id",
            (thread_id,),
        ).fetchall()
    return [_turn_from_row(row) for row in rows]


def thread_actions(db_path: Path, thread_id: str, admin_id: str) -> list[Action]:
    """The thread's action cards, oldest first."""
    get_thread(db_path, thread_id, admin_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM admin_chat_actions WHERE thread_id = ? ORDER BY created_at, action_id",
            (thread_id,),
        ).fetchall()
    return [_action_from_row(row) for row in rows]


def append_message(
    db_path: Path,
    thread_id: str,
    *,
    role: str,
    content: str,
    turn_id: str | None = None,
) -> Message:
    """Append one message and move the thread's ``updated_at`` and title forward."""
    if role not in {"user", "assistant"}:
        raise ValueError(f"unknown message role: {role!r}")
    init_admin_chat(db_path)
    at = _now()
    message_id = _new_id()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM admin_chat_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO admin_chat_messages
                (message_id, thread_id, turn_id, role, seq, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, thread_id, turn_id, role, next_seq, content, at),
        )
        conn.execute(
            "UPDATE admin_chat_threads SET updated_at = ? WHERE thread_id = ?",
            (at, thread_id),
        )
        if role == "user":
            # A thread with no title takes its first user message's first words.
            conn.execute(
                """
                UPDATE admin_chat_threads SET title = ?
                WHERE thread_id = ? AND TRIM(title) = ''
                """,
                (derive_title(content), thread_id),
            )
    return Message(
        message_id=message_id,
        thread_id=thread_id,
        turn_id=turn_id,
        role=role,
        seq=next_seq,
        content=content,
        created_at=at,
    )


def update_assistant_text(db_path: Path, message_id: str, content: str) -> None:
    """Replace the stored text of one assistant message as the reply streams."""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE admin_chat_messages SET content = ? WHERE message_id = ? AND role = 'assistant'",
            (content, message_id),
        )


def start_turn(
    db_path: Path,
    thread_id: str,
    admin_id: str,
    *,
    admin_email: str = "",
    retry_of: str | None = None,
    request_chars: int = 0,
) -> Turn:
    """Open one turn in ``running``. ``retry_of`` must be a turn in the same thread."""
    init_admin_chat(db_path)
    if retry_of is not None:
        _assert_turn_in_thread(db_path, retry_of, thread_id)
    turn_id = _new_id()
    at = _now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO admin_chat_turns (
                turn_id, thread_id, admin_id, admin_email, status, retry_of, error,
                request_chars, first_token_ms, started_at, ended_at
            )
            VALUES (?, ?, ?, ?, 'running', ?, NULL, ?, NULL, ?, NULL)
            """,
            (turn_id, thread_id, admin_id, admin_email, retry_of, request_chars, at),
        )
    return get_turn(db_path, turn_id)


def get_turn(db_path: Path, turn_id: str) -> Turn:
    """One turn by id."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM admin_chat_turns WHERE turn_id = ?", (turn_id,)
        ).fetchone()
    if row is None:
        raise KeyError(turn_id)
    return _turn_from_row(row)


def finish_turn(
    db_path: Path,
    turn_id: str,
    *,
    status: str,
    error: str | None = None,
    first_token_ms: int | None = None,
    private_paths: tuple[str, ...] = (),
) -> None:
    """Settle a turn. The error text is redacted before it is stored."""
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError(f"not a terminal turn status: {status!r}")
    message = redacted_detail(error, private_paths) if error else None
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE admin_chat_turns
            SET status = ?, error = ?, first_token_ms = COALESCE(?, first_token_ms), ended_at = ?
            WHERE turn_id = ?
            """,
            (status, message, first_token_ms, _now(), turn_id),
        )


def fail_interrupted_chat_turns(db_path: Path) -> int:
    """Fail every turn a restart left ``running``. Called from the API lifespan."""
    init_admin_chat(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE admin_chat_turns SET status = 'failed', error = ?, ended_at = ?
            WHERE status = 'running'
            """,
            (INTERRUPTED, _now()),
        )
        return cursor.rowcount


def delete_thread(db_path: Path, thread_id: str, admin_id: str) -> bool:
    """Hard-delete one thread, children before parent. Audit rows stay (D-4).

    Returns ``True`` the first time and ``False`` after that, so the route can
    answer 204 either way while exactly one ``thread_deleted`` row is written.
    """
    init_admin_chat(db_path)
    at = _now()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        thread = conn.execute(
            "SELECT thread_id FROM admin_chat_threads WHERE thread_id = ? AND created_by = ?",
            (thread_id, admin_id),
        ).fetchone()
        if thread is None:
            return False
        counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", (thread_id,)
            ).fetchone()[0]
            for table in (
                "admin_chat_actions",
                "admin_chat_messages",
                "admin_chat_turns",
                "admin_chat_thread_runs",
            )
        }
        # The audit row is written first, while the thread still exists, so the
        # record names a thread even if a later statement were to fail.
        conn.execute(
            """
            INSERT INTO admin_chat_audit
                (at, admin_id, admin_email, thread_id, turn_id, kind, detail)
            VALUES (?, ?, ?, ?, NULL, 'thread_deleted', ?)
            """,
            (
                at,
                admin_id,
                _creator_email(conn, thread_id),
                thread_id,
                redacted_detail(
                    f"thread {thread_id} deleted actions={counts['admin_chat_actions']} "
                    f"messages={counts['admin_chat_messages']} "
                    f"turns={counts['admin_chat_turns']} "
                    f"runs={counts['admin_chat_thread_runs']}"
                ),
            ),
        )
        for table in (
            "admin_chat_actions",
            "admin_chat_messages",
            "admin_chat_turns",
            "admin_chat_thread_runs",
        ):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM admin_chat_threads WHERE thread_id = ?", (thread_id,))
    return True


def _creator_email(conn: sqlite3.Connection, thread_id: str) -> str:
    row = conn.execute(
        "SELECT created_by_email FROM admin_chat_threads WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return str(row["created_by_email"]) if row is not None else ""


def link_run(db_path: Path, thread_id: str, run_id: str) -> ThreadRun:
    """Link a run to a thread. Linking the same pair twice is idempotent."""
    init_admin_chat(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO admin_chat_thread_runs (thread_id, run_id, linked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id, run_id) DO NOTHING
            """,
            (thread_id, run_id, _now()),
        )
        row = conn.execute(
            "SELECT * FROM admin_chat_thread_runs WHERE thread_id = ? AND run_id = ?",
            (thread_id, run_id),
        ).fetchone()
    return ThreadRun(
        thread_id=str(row["thread_id"]),
        run_id=str(row["run_id"]),
        linked_at=str(row["linked_at"]),
    )


def thread_runs(db_path: Path, thread_id: str, admin_id: str) -> list[ThreadRun]:
    """Every run linked to this admin's thread, newest link first."""
    get_thread(db_path, thread_id, admin_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM admin_chat_thread_runs
            WHERE thread_id = ? ORDER BY linked_at DESC, run_id
            """,
            (thread_id,),
        ).fetchall()
    return [
        ThreadRun(
            thread_id=str(row["thread_id"]),
            run_id=str(row["run_id"]),
            linked_at=str(row["linked_at"]),
        )
        for row in rows
    ]


def record_audit(
    db_path: Path,
    *,
    admin_id: str,
    admin_email: str = "",
    kind: str,
    thread_id: str | None = None,
    turn_id: str | None = None,
    detail: str = "",
    private_paths: tuple[str, ...] = (),
) -> int:
    """Write one audit row. ``kind`` is enforced by the table's ``CHECK`` too."""
    if kind not in AUDIT_KINDS:
        raise ValueError(f"unknown audit kind: {kind!r}")
    init_admin_chat(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO admin_chat_audit
                (at, admin_id, admin_email, thread_id, turn_id, kind, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                admin_id,
                admin_email,
                thread_id,
                turn_id,
                kind,
                redacted_detail(detail, private_paths),
            ),
        )
        return int(cursor.lastrowid or 0)


def count_recent_turns(db_path: Path, admin_id: str, window_seconds: float) -> int:
    """Turns this admin started inside the window. Reads ``admin_chat_turns`` only."""
    init_admin_chat(db_path)
    since = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    with connect(db_path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM admin_chat_turns
                WHERE admin_id = ? AND started_at >= ?
                """,
                (admin_id, since),
            ).fetchone()[0]
        )


def prune_audit(
    db_path: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """Delete audit rows older than the window. ``0`` or less keeps them forever.

    Only ``admin_chat_audit`` is touched, and the prune is not itself audited:
    an audit row recording a prune would age out on the same schedule. The
    window is an argument with no default, so no retention literal lives here
    and the caller's setting is the only source.
    """
    if retention_days <= 0:
        return 0
    init_admin_chat(db_path)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = (moment - timedelta(days=retention_days)).isoformat()
    with connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM admin_chat_audit WHERE at < ?", (cutoff,))
        return int(cursor.rowcount or 0)


def _assert_turn_in_thread(db_path: Path, turn_id: str, thread_id: str) -> None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT thread_id FROM admin_chat_turns WHERE turn_id = ?", (turn_id,)
        ).fetchone()
    if row is None or str(row["thread_id"]) != thread_id:
        raise ValueError("The turn being retried is not in this thread.")


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        thread_id=str(row["thread_id"]),
        title=str(row["title"]),
        created_by=str(row["created_by"]),
        created_by_email=str(row["created_by_email"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        message_id=str(row["message_id"]),
        thread_id=str(row["thread_id"]),
        turn_id=None if row["turn_id"] is None else str(row["turn_id"]),
        role=str(row["role"]),
        seq=int(row["seq"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
    )


def _turn_from_row(row: sqlite3.Row) -> Turn:
    return Turn(
        turn_id=str(row["turn_id"]),
        thread_id=str(row["thread_id"]),
        admin_id=str(row["admin_id"]),
        admin_email=str(row["admin_email"]),
        status=str(row["status"]),
        retry_of=None if row["retry_of"] is None else str(row["retry_of"]),
        error=None if row["error"] is None else str(row["error"]),
        request_chars=int(row["request_chars"]),
        first_token_ms=None if row["first_token_ms"] is None else int(row["first_token_ms"]),
        started_at=str(row["started_at"]),
        ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
    )


def _action_from_row(row: sqlite3.Row) -> Action:
    return Action(
        action_id=str(row["action_id"]),
        thread_id=str(row["thread_id"]),
        turn_id=str(row["turn_id"]),
        kind=str(row["kind"]),
        payload=str(row["payload"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
        decided_at=None if row["decided_at"] is None else str(row["decided_at"]),
        decided_by=None if row["decided_by"] is None else str(row["decided_by"]),
        result=None if row["result"] is None else str(row["result"]),
    )

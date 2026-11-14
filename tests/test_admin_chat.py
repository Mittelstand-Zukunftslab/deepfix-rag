"""The admin chat store: schema, ownership, delete order, audit, and retention.

A real SQLite file under ``tmp_path`` throughout. No mocks: the module is the
storage layer, so a fake connection would test nothing worth testing.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from deepfix_rag import admin_chat
from deepfix_rag.optimization import init_optimization
from deepfix_rag.storage import connect

pytestmark = [pytest.mark.unit, pytest.mark.integration]

ADMIN = "admin-1"
OTHER = "admin-2"
CHAT_KEY = "chat-key-0123456789abcdef"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "product.db"
    admin_chat.init_admin_chat(path)
    return path


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]


def test_the_six_tables_have_the_columns_the_stories_use(db_path: Path) -> None:
    with connect(db_path) as conn:
        assert _columns(conn, "admin_chat_threads") == [
            "thread_id",
            "title",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
        ]
        assert _columns(conn, "admin_chat_turns") == [
            "turn_id",
            "thread_id",
            "admin_id",
            "admin_email",
            "status",
            "retry_of",
            "error",
            "request_chars",
            "first_token_ms",
            "started_at",
            "ended_at",
        ]
        assert _columns(conn, "admin_chat_messages") == [
            "message_id",
            "thread_id",
            "turn_id",
            "role",
            "seq",
            "content",
            "created_at",
        ]
        actions = _columns(conn, "admin_chat_actions")
        assert actions == [
            "action_id",
            "thread_id",
            "turn_id",
            "kind",
            "payload",
            "status",
            "created_at",
            "expires_at",
            "decided_at",
            "decided_by",
            "result",
        ]
        assert _columns(conn, "admin_chat_thread_runs") == ["thread_id", "run_id", "linked_at"]
        assert _columns(conn, "admin_chat_audit") == [
            "event_id",
            "at",
            "admin_id",
            "admin_email",
            "thread_id",
            "turn_id",
            "kind",
            "detail",
        ]


def test_unknown_enumerated_values_are_rejected_by_the_schema(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO admin_chat_audit (at, admin_id, kind, detail)
            VALUES ('2026-01-01T00:00:00+00:00', 'a', 'not_a_kind', '')
            """
        )
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO admin_chat_turns
                (turn_id, thread_id, admin_id, status, started_at)
            VALUES ('t', ?, 'a', 'unknown', '2026-01-01T00:00:00+00:00')
            """,
            (thread.thread_id,),
        )
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO admin_chat_actions
                (action_id, thread_id, turn_id, kind, status, created_at, expires_at)
            VALUES ('a1', ?, 't', 'start_optimization_run', 'unknown', 'x', 'y')
            """,
            (thread.thread_id,),
        )
    with pytest.raises(ValueError):
        admin_chat.record_audit(db_path, admin_id=ADMIN, kind="not_a_kind")


def test_init_is_idempotent_and_creates_the_five_indexes(db_path: Path) -> None:
    admin_chat.init_admin_chat(db_path)
    admin_chat.init_admin_chat(db_path)
    with connect(db_path) as conn:
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name LIKE 'admin_chat%'"
            )
        }
    assert {
        "admin_chat_threads_by_admin",
        "admin_chat_messages_by_thread",
        "admin_chat_turns_by_thread",
        "admin_chat_turns_by_admin",
        "admin_chat_actions_by_thread",
    } <= indexes


def test_the_audit_table_has_no_foreign_key_to_threads_and_runs_none_to_runs(
    db_path: Path,
) -> None:
    with connect(db_path) as conn:
        audit_keys = conn.execute("PRAGMA foreign_key_list(admin_chat_audit)").fetchall()
        run_keys = conn.execute("PRAGMA foreign_key_list(admin_chat_thread_runs)").fetchall()
    assert [dict(row) for row in audit_keys] == []
    assert [dict(row) for row in run_keys] == []


def test_threads_are_visible_only_to_the_admin_who_created_them(db_path: Path) -> None:
    mine = admin_chat.create_thread(db_path, ADMIN, admin_email="a@example.com")
    admin_chat.create_thread(db_path, OTHER, admin_email="b@example.com")
    admin_chat.append_message(db_path, mine.thread_id, role="user", content="hello there")

    assert [thread.thread_id for thread in admin_chat.list_threads(db_path, ADMIN)] == [
        mine.thread_id
    ]
    with pytest.raises(KeyError):
        admin_chat.get_thread(db_path, mine.thread_id, OTHER)
    with pytest.raises(KeyError):
        admin_chat.thread_messages(db_path, mine.thread_id, OTHER)
    with pytest.raises(KeyError):
        admin_chat.thread_turns(db_path, mine.thread_id, OTHER)
    assert admin_chat.thread_messages(db_path, mine.thread_id, ADMIN)[0].content == "hello there"


def test_a_thread_takes_its_title_from_its_first_user_message(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    assert thread.title == ""
    admin_chat.append_message(
        db_path, thread.thread_id, role="user", content="  Why   did\n\nlast night's run fail?  "
    )
    stored = admin_chat.get_thread(db_path, thread.thread_id, ADMIN)
    assert stored.title == "Why did last night's run fail?"
    admin_chat.append_message(db_path, thread.thread_id, role="user", content="a second question")
    assert admin_chat.get_thread(db_path, thread.thread_id, ADMIN).title == (
        "Why did last night's run fail?"
    )


def test_a_long_first_message_is_cut_to_sixty_characters(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    admin_chat.append_message(db_path, thread.thread_id, role="user", content="word " * 40)
    title = admin_chat.get_thread(db_path, thread.thread_id, ADMIN).title
    assert len(title) <= admin_chat.TITLE_CHARS
    assert title == admin_chat.derive_title("word " * 40)
    assert title == ("word " * 12).strip()
    assert not title.endswith(" ")


def test_threads_are_listed_newest_first_and_capped(db_path: Path) -> None:
    first = admin_chat.create_thread(db_path, ADMIN)
    second = admin_chat.create_thread(db_path, ADMIN)
    admin_chat.append_message(db_path, first.thread_id, role="user", content="newest")
    listed = admin_chat.list_threads(db_path, ADMIN)
    assert [thread.thread_id for thread in listed] == [first.thread_id, second.thread_id]
    assert len(admin_chat.list_threads(db_path, ADMIN, limit=1)) == 1


def test_a_turn_round_trips_and_honours_retry_of(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    turn = admin_chat.start_turn(db_path, thread.thread_id, ADMIN, request_chars=12)
    assert turn.status == "running"
    admin_chat.finish_turn(db_path, turn.turn_id, status="completed", first_token_ms=240)
    stored = admin_chat.get_turn(db_path, turn.turn_id)
    assert stored.status == "completed"
    assert stored.first_token_ms == 240
    assert stored.ended_at is not None

    retry = admin_chat.start_turn(db_path, thread.thread_id, ADMIN, retry_of=turn.turn_id)
    assert retry.retry_of == turn.turn_id
    other_thread = admin_chat.create_thread(db_path, ADMIN)
    with pytest.raises(ValueError):
        admin_chat.start_turn(db_path, other_thread.thread_id, ADMIN, retry_of=turn.turn_id)


def test_count_recent_turns_reads_turns_only(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    admin_chat.start_turn(db_path, thread.thread_id, ADMIN)
    admin_chat.record_audit(db_path, admin_id=ADMIN, kind="rate_limited", detail="x")
    assert admin_chat.count_recent_turns(db_path, ADMIN, 300) == 1
    assert admin_chat.count_recent_turns(db_path, OTHER, 300) == 0


def test_an_audit_detail_is_redacted_and_capped(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_CHAT_API_SERVER_KEY", CHAT_KEY)
    event_id = admin_chat.record_audit(
        db_path,
        admin_id=ADMIN,
        kind="turn_failed",
        detail=f"upstream said {CHAT_KEY} and then " + "x" * 800,
    )
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT detail FROM admin_chat_audit WHERE event_id = ?", (event_id,)
        ).fetchone()
    detail = str(row["detail"])
    assert "[redacted]" in detail
    assert CHAT_KEY not in detail
    assert len(detail) <= 500


def test_redact_removes_the_chat_key_and_private_paths(tmp_path: Path) -> None:
    private = tmp_path / "private" / "product.db"
    text = f"key={CHAT_KEY} path={private} provider=sk-or-abcdef123456"
    cleaned = admin_chat.redact(
        text,
        private_paths=(str(tmp_path / "private"),),
        secrets=(CHAT_KEY, "sk-or-abcdef123456"),
    )
    assert CHAT_KEY not in cleaned
    assert str(tmp_path / "private") not in cleaned
    assert "sk-or-abcdef123456" not in cleaned
    assert cleaned.count("[redacted]") == 3


def test_delete_is_a_hard_delete_that_audits_and_keeps_audit_rows(db_path: Path) -> None:
    init_optimization(db_path)
    thread = admin_chat.create_thread(db_path, ADMIN, admin_email="a@example.com")
    turn = admin_chat.start_turn(db_path, thread.thread_id, ADMIN)
    admin_chat.append_message(db_path, thread.thread_id, role="user", content="start a run")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO admin_chat_actions
                (action_id, thread_id, turn_id, kind, status, created_at, expires_at)
            VALUES ('a1', ?, ?, 'start_optimization_run', 'proposed', 'x', 'y')
            """,
            (thread.thread_id, turn.turn_id),
        )
        conn.execute(
            "INSERT INTO optimization_runs (run_id, status, trigger_kind, snapshot_id,"
            " dev_count, new_dev_count, holdout_count, dev_query_ids, started_at)"
            " VALUES ('run-1', 'succeeded', 'manual', 'snap', 0, 0, 0, '[]', 't')"
        )
    admin_chat.link_run(db_path, thread.thread_id, "run-1")
    admin_chat.record_audit(
        db_path, admin_id=ADMIN, kind="turn_started", thread_id=thread.thread_id, detail="hi"
    )

    assert admin_chat.delete_thread(db_path, thread.thread_id, ADMIN) is True
    assert admin_chat.delete_thread(db_path, thread.thread_id, ADMIN) is False

    with connect(db_path) as conn:
        for table in (
            "admin_chat_actions",
            "admin_chat_messages",
            "admin_chat_turns",
            "admin_chat_thread_runs",
            "admin_chat_threads",
        ):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, table
        deleted = conn.execute(
            "SELECT COUNT(*) FROM admin_chat_audit WHERE kind = 'thread_deleted'"
        ).fetchone()[0]
        survivors = conn.execute(
            "SELECT kind, thread_id FROM admin_chat_audit WHERE kind != 'thread_deleted'"
        ).fetchall()
        runs = conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]
    assert deleted == 1
    assert [str(row["kind"]) for row in survivors] == ["turn_started"]
    assert str(survivors[0]["thread_id"]) == thread.thread_id
    assert runs == 1


def test_the_thread_deleted_row_names_what_was_removed(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN, admin_email="a@example.com")
    admin_chat.start_turn(db_path, thread.thread_id, ADMIN)
    admin_chat.append_message(db_path, thread.thread_id, role="user", content="hello")
    admin_chat.delete_thread(db_path, thread.thread_id, ADMIN)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT admin_email, detail FROM admin_chat_audit WHERE kind = 'thread_deleted'"
        ).fetchone()
    assert str(row["admin_email"]) == "a@example.com"
    detail = str(row["detail"])
    assert f"thread {thread.thread_id} deleted" in detail
    assert "messages=1" in detail
    assert "turns=1" in detail


def test_an_interrupted_turn_never_stays_running(db_path: Path) -> None:
    thread = admin_chat.create_thread(db_path, ADMIN)
    turn = admin_chat.start_turn(db_path, thread.thread_id, ADMIN)
    assert admin_chat.fail_interrupted_chat_turns(db_path) == 1
    stored = admin_chat.get_turn(db_path, turn.turn_id)
    assert stored.status == "failed"
    assert stored.error == admin_chat.INTERRUPTED
    assert admin_chat.fail_interrupted_chat_turns(db_path) == 0


def test_prune_deletes_only_rows_past_the_window(db_path: Path) -> None:
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    for days in (1, 13, 15, 400):
        admin_chat.record_audit(db_path, admin_id=ADMIN, kind="turn_started", detail=f"{days}d")
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE admin_chat_audit SET at = ? WHERE detail = ?",
                ((now - timedelta(days=days)).isoformat(), f"{days}d"),
            )
    assert admin_chat.prune_audit(db_path, 14, now=now) == 2
    with connect(db_path) as conn:
        kept = [
            str(row["detail"])
            for row in conn.execute("SELECT detail FROM admin_chat_audit ORDER BY detail")
        ]
    assert kept == ["13d", "1d"]
    assert admin_chat.prune_audit(db_path, 14, now=now) == 0


def test_prune_with_zero_or_negative_retention_deletes_nothing(db_path: Path) -> None:
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    admin_chat.record_audit(db_path, admin_id=ADMIN, kind="turn_started", detail="old")
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE admin_chat_audit SET at = ?",
            ((now - timedelta(days=4000)).isoformat(),),
        )
    assert admin_chat.prune_audit(db_path, 0, now=now) == 0
    assert admin_chat.prune_audit(db_path, -3, now=now) == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_chat_audit").fetchone()[0] == 1


def test_a_prune_touches_only_the_audit_table(db_path: Path) -> None:
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    init_optimization(db_path)
    thread = admin_chat.create_thread(db_path, ADMIN)
    admin_chat.append_message(db_path, thread.thread_id, role="user", content="keep me")
    admin_chat.start_turn(db_path, thread.thread_id, ADMIN)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO optimization_runs (run_id, status, trigger_kind, snapshot_id,"
            " dev_count, new_dev_count, holdout_count, dev_query_ids, started_at)"
            " VALUES ('run-1', 'succeeded', 'manual', 'snap', 0, 0, 0, '[]', 't')"
        )
    admin_chat.record_audit(db_path, admin_id=ADMIN, kind="turn_started", detail="old")
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE admin_chat_audit SET at = ?", ((now - timedelta(days=90)).isoformat(),)
        )
    admin_chat.prune_audit(db_path, 14, now=now)
    with connect(db_path) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "admin_chat_audit",
                "admin_chat_threads",
                "admin_chat_messages",
                "admin_chat_turns",
                "admin_chat_actions",
                "admin_chat_thread_runs",
                "optimization_runs",
            )
        }
    assert counts == {
        "admin_chat_audit": 0,
        "admin_chat_threads": 1,
        "admin_chat_messages": 1,
        "admin_chat_turns": 1,
        "admin_chat_actions": 0,
        "admin_chat_thread_runs": 0,
        "optimization_runs": 1,
    }


def test_prune_compares_the_stored_iso_timestamp(db_path: Path) -> None:
    """A row written by ``record_audit`` is pruned on its real age, not never."""
    admin_chat.record_audit(db_path, admin_id=ADMIN, kind="turn_started", detail="written now")
    assert admin_chat.prune_audit(db_path, 1) == 0
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert admin_chat.prune_audit(db_path, 1, now=later) == 1


def test_the_two_thread_load_queries_use_their_indexes(db_path: Path) -> None:
    admin_chat.create_thread(db_path, ADMIN)
    with connect(db_path) as conn:
        messages = conn.execute(
            "EXPLAIN QUERY PLAN " + admin_chat.MESSAGES_BY_THREAD_SQL, ("x",)
        ).fetchall()
        threads = conn.execute(
            "EXPLAIN QUERY PLAN " + admin_chat.THREADS_BY_ADMIN_SQL, (ADMIN, 50)
        ).fetchall()
    assert any("USING INDEX" in str(row["detail"]) for row in messages)
    assert any("USING INDEX" in str(row["detail"]) for row in threads)

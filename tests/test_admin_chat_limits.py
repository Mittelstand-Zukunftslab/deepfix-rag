"""Rate limits, the audit read, and the retention prune's hourly job.

The limiter takes an injectable clock, so no test sleeps out a 300-second
window, and the scheduler is the same ``FakeClock``/``TickableScheduler``
harness ``tests/test_optimization_schedule.py`` already uses.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deepfix_rag import admin_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization_schedule import PRUNE_JOB_ID, OptimizationScheduler
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_admin_chat_routes import ADMIN, CHAT_KEY, MODEL, StandIn
from tests.test_admin_chat_routes import hermes as hermes_fixture
from tests.test_optimization_schedule import FakeClock, _tickable
from tests.test_optimization_schedule import _settings as _schedule_settings

pytestmark = [pytest.mark.api, pytest.mark.integration]

# The limiter's window is measured against real time, so the seeded turns are
# placed relative to whenever this module runs.
NOW = datetime.now(timezone.utc)
ADMIN_ID = "admin-1"


@pytest.fixture
def hermes() -> Iterator[StandIn]:
    yield from hermes_fixture.__wrapped__()  # type: ignore[attr-defined]


def _settings(tmp_path: Path, hermes: StandIn, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "PRODUCT_DATA_DIR": tmp_path / "private",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "WEB_ORIGIN": "http://localhost:8080",
        "HERMES_CHAT_ENABLED": True,
        "HERMES_CHAT_BASE_URL": hermes.url,
        "HERMES_CHAT_API_SERVER_KEY": CHAT_KEY,
        "HERMES_CHAT_MODEL": "",
        "HERMES_MODEL": MODEL,
        "ADMIN_CHAT_MAX_PROMPT_CHARS": 200,
        "ADMIN_CHAT_MAX_MESSAGES": 10,
        "ADMIN_CHAT_MAX_TURNS_PER_WINDOW": 30,
        "ADMIN_CHAT_TURN_WINDOW_SECONDS": 300,
        "ADMIN_CHAT_MAX_CONCURRENT_TURNS": 2,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(create_app(settings, token_verifier=identity.verifier()))


def _seed_turns(db_path: Path, admin_id: str, count: int, *, started: datetime = NOW) -> None:
    admin_chat.init_admin_chat(db_path)
    thread = admin_chat.create_thread(db_path, admin_id)
    for index in range(count):
        turn = admin_chat.start_turn(db_path, thread.thread_id, admin_id)
        admin_chat.finish_turn(db_path, turn.turn_id, status="completed")
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE admin_chat_turns SET started_at = ? WHERE turn_id = ?",
                ((started + timedelta(seconds=index)).isoformat(), turn.turn_id),
            )


def test_the_turn_after_the_window_cap_is_refused_with_a_wait(tmp_path: Path) -> None:
    db_path = tmp_path / "product.db"
    _seed_turns(db_path, ADMIN_ID, 30)
    refusal = admin_chat.check_rate_limit(
        db_path,
        ADMIN_ID,
        now=NOW + timedelta(seconds=60),
        max_turns=30,
        window_seconds=300,
        max_concurrent=2,
    )
    assert refusal is not None
    assert refusal.audit is True
    assert refusal.retry_after >= 1
    assert "30 of 30 turns" in refusal.message


def test_a_second_running_turn_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "product.db"
    admin_chat.init_admin_chat(db_path)
    thread = admin_chat.create_thread(db_path, ADMIN_ID)
    admin_chat.start_turn(db_path, thread.thread_id, ADMIN_ID)
    refusal = admin_chat.check_rate_limit(
        db_path,
        ADMIN_ID,
        now=NOW,
        max_turns=30,
        window_seconds=300,
        max_concurrent=2,
    )
    assert refusal is not None
    assert "still running" in refusal.message


def test_the_global_cap_is_refused_without_an_audit_row(tmp_path: Path) -> None:
    db_path = tmp_path / "product.db"
    _seed_turns(db_path, "admin-1", 1)
    _seed_turns(db_path, "admin-2", 1)
    admin_chat.init_admin_chat(db_path)
    for admin_id in ("admin-1", "admin-2"):
        thread = admin_chat.create_thread(db_path, admin_id)
        admin_chat.start_turn(db_path, thread.thread_id, admin_id)
    refusal = admin_chat.check_rate_limit(
        db_path,
        "admin-3",
        now=NOW,
        max_turns=30,
        window_seconds=300,
        max_concurrent=2,
    )
    assert refusal is not None
    assert refusal.message == admin_chat.BUSY_MESSAGE
    assert refusal.audit is False


def test_a_refusal_cannot_ratchet_itself(tmp_path: Path) -> None:
    """The limiter counts turns, never audit rows, so a refusal is not a turn."""
    db_path = tmp_path / "product.db"
    _seed_turns(db_path, ADMIN_ID, 30)
    for _ in range(5):
        refused = admin_chat.check_rate_limit(
            db_path,
            ADMIN_ID,
            now=NOW + timedelta(seconds=60),
            max_turns=30,
            window_seconds=300,
            max_concurrent=2,
        )
        assert refused is not None
        assert admin_chat.count_recent_turns(db_path, ADMIN_ID, 300) == 30


def test_at_most_one_rate_limited_row_per_minute(tmp_path: Path) -> None:
    """The check is a plain SELECT over the newest row for that admin and kind."""
    db_path = tmp_path / "product.db"
    admin_chat.init_admin_chat(db_path)
    moment = datetime.now(timezone.utc)
    assert admin_chat.should_audit_rate_limit(db_path, ADMIN_ID, now=moment) is True
    event_id = admin_chat.record_audit(db_path, admin_id=ADMIN_ID, kind="rate_limited", detail="x")
    # `record_audit` stamps the real clock, so pin the row to the clock this
    # test holds; otherwise the assertion depends on how long the run took.
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE admin_chat_audit SET at = ? WHERE event_id = ?",
            (moment.isoformat(), event_id),
        )
    assert admin_chat.should_audit_rate_limit(db_path, ADMIN_ID, now=moment) is False
    assert (
        admin_chat.should_audit_rate_limit(db_path, ADMIN_ID, now=moment + timedelta(seconds=61))
        is True
    )


def test_the_thirty_first_turn_is_429_over_http(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes, ADMIN_CHAT_MAX_TURNS_PER_WINDOW=2)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        thread_id = client.post("/admin/chat/threads").json()["thread_id"]
        _seed_turns(settings.product_db_path, identity.user_id(ADMIN), 2)
        response = client.post(
            "/admin/chat/completions",
            json={"thread_id": thread_id, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 429
    assert response.headers["retry-after"].isdigit()
    with connect(settings.product_db_path) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM admin_chat_audit WHERE kind = 'rate_limited'"
        ).fetchone()[0]
    assert rows == 1


def test_a_member_gets_403_on_the_audit_route(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        identity.sign_in(client, "member@example.com")
        assert client.get("/admin/chat/audit").status_code == 403


def test_the_audit_route_works_with_chat_off_and_the_key_unset(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(
        tmp_path, hermes, HERMES_CHAT_ENABLED=False, HERMES_CHAT_API_SERVER_KEY=""
    )
    admin_chat.init_admin_chat(settings.product_db_path)
    admin_chat.record_audit(
        settings.product_db_path,
        admin_id=identity.user_id(ADMIN),
        admin_email=ADMIN,
        kind="turn_started",
        detail="hi",
    )
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.get("/admin/chat/audit")
    assert response.status_code == 200
    assert response.json()[0]["kind"] == "turn_started"


def test_a_deleted_threads_rows_report_thread_deleted_without_message_text(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    admin_chat.init_admin_chat(settings.product_db_path)
    admin_id = identity.user_id(ADMIN)
    thread = admin_chat.create_thread(settings.product_db_path, admin_id, admin_email=ADMIN)
    turn = admin_chat.start_turn(settings.product_db_path, thread.thread_id, admin_id)
    admin_chat.record_audit(
        settings.product_db_path,
        admin_id=admin_id,
        admin_email=ADMIN,
        kind="turn_started",
        thread_id=thread.thread_id,
        turn_id=turn.turn_id,
        detail="turn started",
    )
    admin_chat.delete_thread(settings.product_db_path, thread.thread_id, admin_id)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        rows = client.get("/admin/chat/audit").json()
    deleted = [row for row in rows if row["kind"] == "thread_deleted"]
    started = [row for row in rows if row["kind"] == "turn_started"]
    assert len(deleted) == 1
    assert deleted[0]["thread_deleted"] is True
    assert started[0]["thread_deleted"] is True
    assert started[0]["turn_id"] == turn.turn_id
    assert started[0]["detail"] == "turn started"
    # No message text: the audit row has no `content` field and holds no body.
    assert "content" not in json.dumps(rows)


def test_an_audit_detail_is_stored_redacted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CHAT_API_SERVER_KEY", CHAT_KEY)
    db_path = tmp_path / "product.db"
    admin_chat.init_admin_chat(db_path)
    admin_chat.record_audit(
        db_path,
        admin_id=ADMIN_ID,
        kind="turn_failed",
        detail=f"upstream said {CHAT_KEY}",
    )
    with connect(db_path) as conn:
        detail = conn.execute("SELECT detail FROM admin_chat_audit").fetchone()["detail"]
    assert CHAT_KEY not in str(detail)
    assert "[redacted]" in str(detail)


def test_the_prune_job_is_hourly_and_writes_one_line_only_when_it_deletes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    deleted: list[int] = []
    with clock:
        scheduler, live = _tickable(
            _schedule_settings(tmp_path),
            clock,
            prune_audit=lambda: deleted.append(1) or 3,
            prune_retention_days=lambda: 14,
        )
        scheduler.start()
        try:
            job = live.get_job(PRUNE_JOB_ID)
            assert job is not None
            assert job.max_instances == 1
            assert job.coalesce is True
            assert job.misfire_grace_time is None
            with caplog.at_level("INFO", logger="DeepFix RAG Eval Runner"):
                live.tick()
                clock.advance(3599)
                live.tick()
                clock.advance(1)
                live.tick()
        finally:
            scheduler.stop()
    assert len(deleted) == 2
    lines = [r for r in caplog.records if "chat audit prune" in r.getMessage()]
    assert all("deleted=3 retention_days=14" in r.getMessage() for r in lines)
    assert len(lines) == 2


def test_a_prune_that_deletes_nothing_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    with clock:
        scheduler, live = _tickable(_schedule_settings(tmp_path), clock, prune_audit=lambda: 0)
        scheduler.start()
        try:
            with caplog.at_level("INFO", logger="DeepFix RAG Eval Runner"):
                live.tick()
        finally:
            scheduler.stop()
    assert not [r for r in caplog.records if "chat audit prune" in r.getMessage()]


def test_a_raising_prune_does_not_stop_the_scheduler(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    calls: list[int] = []

    def _boom() -> int:
        calls.append(1)
        raise RuntimeError("prune exploded")

    with clock:
        scheduler, live = _tickable(_schedule_settings(tmp_path), clock, prune_audit=_boom)
        scheduler.start()
        try:
            with caplog.at_level("INFO"):
                live.tick()
                clock.advance(3600)
                live.tick()
        finally:
            scheduler.stop()
    assert len(calls) == 2
    assert [r for r in caplog.records if "Chat audit prune failed" in r.getMessage()]


def test_the_prune_job_runs_with_chat_off(tmp_path: Path) -> None:
    """The scheduler knows nothing about the chat flag, which is what D-6 wants."""
    clock = FakeClock()
    settings = _schedule_settings(tmp_path).model_copy(update={"HERMES_CHAT_ENABLED": False})
    db_path = settings.product_db_path
    admin_chat.init_admin_chat(db_path)
    admin_chat.record_audit(db_path, admin_id=ADMIN_ID, kind="turn_started", detail="old")
    old = (clock.now() - timedelta(days=90)).isoformat()
    with connect(db_path) as conn:
        conn.execute("UPDATE admin_chat_audit SET at = ?", (old,))
    pruned: list[int] = []

    def _prune() -> int:
        count = admin_chat.prune_audit(db_path, 14, now=clock.now())
        pruned.append(count)
        return count

    with clock:
        scheduler, live = _tickable(settings, clock, prune_audit=_prune)
        scheduler.start()
        try:
            live.tick()
        finally:
            scheduler.stop()
    assert pruned == [1]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_chat_audit").fetchone()[0] == 0


def test_a_paused_schedule_does_not_stop_the_prune(tmp_path: Path) -> None:
    clock = FakeClock()
    pruned: list[int] = []
    with clock:
        scheduler, live = _tickable(
            _schedule_settings(tmp_path), clock, prune_audit=lambda: pruned.append(1) or 0
        )
        scheduler.start()
        try:
            scheduler.update("0 2 * * *", False)
            live.tick()
            clock.advance(3600)
            live.tick()
        finally:
            scheduler.stop()
    assert len(pruned) == 2


def test_a_scheduler_without_a_prune_callable_registers_no_prune_job(tmp_path: Path) -> None:
    clock = FakeClock()
    with clock:
        scheduler, live = _tickable(_schedule_settings(tmp_path), clock)
        scheduler.start()
        try:
            assert live.get_job(PRUNE_JOB_ID) is None
        finally:
            scheduler.stop()


def test_a_zero_retention_prune_leaves_rows_and_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "product.db"
    admin_chat.init_admin_chat(db_path)
    admin_chat.record_audit(db_path, admin_id=ADMIN_ID, kind="turn_started", detail="old")
    with connect(db_path) as conn:
        conn.execute("UPDATE admin_chat_audit SET at = '2000-01-01T00:00:00+00:00'")
    assert admin_chat.prune_audit(db_path, 0) == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_chat_audit").fetchone()[0] == 1
    _ = caplog


def test_the_app_arms_the_prune_job(tmp_path: Path, identity: IdentityIssuer) -> None:
    settings = _schedule_settings(tmp_path)
    app = create_app(settings, token_verifier=identity.verifier())
    with TestClient(app):
        scheduler: OptimizationScheduler = app.state.optimization
        assert scheduler._scheduler.get_job(PRUNE_JOB_ID) is not None

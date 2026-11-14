"""The nightly job uses the same start function as Run now.

Few, end-to-end cases: the real ``BackgroundScheduler``, the real SQLite
database, and the real FastAPI lifespan. Only the passage of time is replaced —
``FakeClock`` stands in for the ``datetime.now`` APScheduler reads and
``TickableScheduler.tick`` runs one real ``_process_jobs`` pass — because waiting
for 02:00 is not a test.

``skip`` is the expected run status in these fixtures: the sample corpus has no
labeled dev questions, which is still enough to prove a fire time reached
``start_optimization_run``.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any
from zoneinfo import ZoneInfo

import apscheduler.executors.base as executor_module
from apscheduler.executors.debug import DebugExecutor
import apscheduler.schedulers.background as background_module
import apscheduler.schedulers.base as base_module
from apscheduler.schedulers.base import STATE_RUNNING, BaseScheduler
from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization, recent_runs, settings_from
from deepfix_rag.optimization_schedule import (
    DEFAULT_CRON,
    DEFAULT_TIMEZONE,
    MISFIRE_GRACE_SECONDS,
    OPTIMIZATION_JOB_ID,
    PRUNE_JOB_ID,
    InvalidSchedule,
    OptimizationScheduler,
    compile_cron,
    next_run,
)
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = pytest.mark.integration

BERLIN = ZoneInfo(DEFAULT_TIMEZONE)
# Saturday 14 November 2026, 15:30 in Berlin — not a fire time of the default
# 02:00 cron, so a scheduler armed "now" begins from a quiet moment.
NOW = datetime(2026, 11, 14, 14, 30, tzinfo=timezone.utc)
# The next 02:00 Berlin after NOW.
TONIGHT = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)


class FakeClock:
    """A hand-driven clock that APScheduler reads as its own ``now``."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> datetime:
        self.moment += timedelta(seconds=seconds)
        return self.moment

    def __enter__(self) -> "FakeClock":
        class _PatchedDateTime(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
                return clock.moment if tz is None else clock.moment.astimezone(tz)

        clock = self
        self._saved = (base_module.datetime, executor_module.datetime)
        base_module.datetime = _PatchedDateTime
        executor_module.datetime = _PatchedDateTime
        return self

    def __exit__(self, *_: object) -> None:
        base_module.datetime, executor_module.datetime = self._saved


class TickableScheduler(background_module.BackgroundScheduler):
    """A ``BackgroundScheduler`` started without its thread and driven by ``tick``.

    Starting the thread would make the test wait on a wall clock it does not
    control. Everything else is the production object: the same trigger, job
    store, job defaults, and ``_process_jobs`` pass. ``DebugExecutor`` runs the
    job inline, so a tick returns only after the job body has run.
    """

    def start(self, *args: Any, **kwargs: Any) -> None:
        # ``BlockingScheduler.start`` normally creates this event; the base class
        # calls ``wakeup`` once, so it must exist even with no thread to wake.
        self._event = threading.Event()
        BaseScheduler.start(self, *args, **kwargs)

    def tick(self) -> float | None:
        assert self.state == STATE_RUNNING
        return self._process_jobs()

    def shutdown(self, wait: bool = True) -> None:
        BaseScheduler.shutdown(self, wait=False)
        self._event.set()


def _settings(tmp_path: Path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def _tickable(
    settings: Settings, clock: FakeClock, **kwargs: Any
) -> tuple[OptimizationScheduler, TickableScheduler]:
    scheduler = OptimizationScheduler(
        settings.product_db_path,
        settings.workspace_dir,
        options_for=lambda: settings_from(settings),
        coordinator_for=lambda: None,
        scheduler_for=lambda: TickableScheduler(
            timezone=timezone.utc, executors={"default": DebugExecutor()}
        ),
        clock=clock.now,
        **kwargs,
    )
    live = scheduler._scheduler
    assert isinstance(live, TickableScheduler)
    return scheduler, live


def _cron_runs(settings: Settings) -> list[str]:
    return [run.status for run in recent_runs(settings.product_db_path) if run.trigger == "cron"]


def test_the_default_fire_time_is_two_am_in_berlin() -> None:
    after = datetime(2026, 10, 12, 12, 0, tzinfo=BERLIN)
    local = next_run(DEFAULT_CRON, after=after, timezone_name=DEFAULT_TIMEZONE).astimezone(BERLIN)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 10, 13, 2, 0)


def test_an_invalid_expression_is_rejected() -> None:
    with pytest.raises(InvalidSchedule, match="five cron fields"):
        compile_cron("60 2 * * *")
    with pytest.raises(InvalidSchedule, match="five cron fields"):
        compile_cron("* * *")


def test_a_weekday_cron_fires_on_that_weekday_in_berlin(tmp_path: Path) -> None:
    """APScheduler numbers weekdays from Monday and cron from Sunday.

    A shift bug here fires the nightly run on the wrong day, so the armed trigger
    is checked to land on the named weekday and the stated local time.
    """
    clock = FakeClock()
    settings = _settings(tmp_path)
    with clock:
        scheduler, _live = _tickable(settings, clock)
        try:
            trigger = scheduler._cron_trigger("15 3 * * 1")
            fire = trigger.get_next_fire_time(None, NOW)
            assert fire is not None
            local = fire.astimezone(BERLIN)
            assert local.weekday() == 0
            assert (local.hour, local.minute) == (3, 15)
        finally:
            scheduler.stop()


def test_one_due_fire_time_starts_exactly_one_run(tmp_path: Path) -> None:
    """A due job runs once; a tick in the same minute or the minute before does not."""
    clock = FakeClock()
    settings = _settings(tmp_path)
    with clock:
        scheduler, live = _tickable(settings, clock)
        scheduler.start()
        try:
            job = scheduler.job
            assert job is not None
            assert job.max_instances == 1
            assert job.coalesce is True
            assert job.misfire_grace_time == MISFIRE_GRACE_SECONDS
            assert job.next_run_time == TONIGHT

            live.tick()
            assert _cron_runs(settings) == []

            clock.advance((TONIGHT - clock.now()).total_seconds())
            live.tick()
            assert _cron_runs(settings) == ["skipped"]

            live.tick()
            clock.advance(60)
            live.tick()
            assert _cron_runs(settings) == ["skipped"]
            assert scheduler.job is not None
            assert scheduler.job.next_run_time == TONIGHT + timedelta(days=1)
        finally:
            scheduler.stop()


def test_a_real_run_row_still_blocks_a_second_scheduled_run(tmp_path: Path) -> None:
    """``max_instances=1`` only serialises this job; ``RunInProgress`` is what holds."""
    clock = FakeClock()
    settings = _settings(tmp_path)
    with clock:
        scheduler, live = _tickable(settings, clock)
        scheduler.start()
        try:
            init_optimization(settings.product_db_path)
            with connect(settings.product_db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO optimization_runs (
                        run_id, status, trigger_kind, reason, snapshot_id, dev_count,
                        new_dev_count, holdout_count, dev_query_ids, started_at
                    )
                    VALUES ('active', 'running', 'manual', NULL, 'snap', 0, 0, 0, '[]',
                            '2099-01-01T00:00:00Z')
                    """
                )
            clock.advance((TONIGHT - clock.now()).total_seconds())
            live.tick()

            assert _cron_runs(settings) == []
            assert recent_runs(settings.product_db_path)[0].run_id == "active"
            assert live.state == STATE_RUNNING
        finally:
            scheduler.stop()


def test_edits_pause_and_resume_take_effect_on_the_live_scheduler(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """The bug: the loop slept to the old next-run time, so an edit was missed.

    Each edit goes through the admin route and is then read back off the live
    APScheduler job, which is the thing that actually fires.
    """
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        scheduler: OptimizationScheduler = client.app.state.optimization

        edited = client.patch("/admin/optimization", json={"cron": "15 3 * * *", "enabled": True})
        assert edited.status_code == 200, edited.text
        job = scheduler.job
        assert job is not None
        assert job.next_run_time is not None
        # The live job moved to the new cron, not just the stored row.
        local = job.next_run_time.astimezone(BERLIN)
        assert (local.hour, local.minute) == (3, 15)
        armed = job.next_run_time.astimezone(timezone.utc).isoformat()
        assert edited.json()["next_run_at"] == armed

        paused = client.patch("/admin/optimization", json={"cron": "15 3 * * *", "enabled": False})
        assert paused.status_code == 200, paused.text
        assert paused.json()["next_run_at"] is None
        assert scheduler.job is not None
        assert scheduler.job.next_run_time is None

        resumed = client.patch("/admin/optimization", json={"cron": "15 3 * * *", "enabled": True})
        assert resumed.status_code == 200, resumed.text
        assert scheduler.job is not None
        assert scheduler.job.next_run_time is not None
        assert (
            resumed.json()["next_run_at"]
            == scheduler.job.next_run_time.astimezone(timezone.utc).isoformat()
        )

        rejected = client.patch("/admin/optimization", json={"cron": "not a cron"})
        assert rejected.status_code == 400
        # A refused edit leaves the live job exactly where it was.
        assert scheduler.job is not None
        assert scheduler.job.next_run_time is not None
        status = client.get("/admin/optimization").json()
        assert status["cron"] == "15 3 * * *"
        assert status["enabled"] is True


def test_a_saved_schedule_is_rebuilt_from_the_row_on_restart(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """A restart arms the job from the stored row, paused included."""
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        saved = client.patch("/admin/optimization", json={"cron": "15 3 * * *", "enabled": False})
        assert saved.status_code == 200, saved.text

    with TestClient(create_app(settings, token_verifier=identity.verifier())) as restarted:
        identity.sign_in(restarted, "admin@example.com", "admin")
        body = restarted.get("/admin/optimization").json()
        assert body["cron"] == "15 3 * * *"
        assert body["enabled"] is False
        assert body["next_run_at"] is None
        assert restarted.app.state.optimization.job is not None
        assert restarted.app.state.optimization.job.next_run_time is None


def test_the_audit_prune_is_an_hourly_job_on_the_same_scheduler(tmp_path: Path) -> None:
    """The prune is its own interval job, so pausing the run does not stop it."""
    clock = FakeClock()
    settings = _settings(tmp_path)
    pruned: list[datetime] = []
    with clock:
        scheduler, live = _tickable(
            settings, clock, prune_audit=lambda: pruned.append(clock.now())
        )
        scheduler.start()
        try:
            job = live.get_job(PRUNE_JOB_ID)
            assert job is not None
            assert job.max_instances == 1
            assert job.coalesce is True

            scheduler.update(DEFAULT_CRON, False)
            live.tick()
            assert len(pruned) == 1
            clock.advance(3599)
            live.tick()
            assert len(pruned) == 1
            clock.advance(1)
            live.tick()
            assert len(pruned) == 2
        finally:
            scheduler.stop()


def test_the_scheduler_starts_once_and_shuts_down_with_the_app(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """One scheduler per process: started by the lifespan, joined on exit."""
    settings = _settings(tmp_path)
    app = create_app(settings, token_verifier=identity.verifier())
    with TestClient(app):
        scheduler: OptimizationScheduler = app.state.optimization
        thread = scheduler._scheduler._thread
        assert scheduler.running is True
        assert thread is not None and thread.is_alive() and thread.daemon
        # A re-entrant start must not add a second scheduler or a second job.
        scheduler.start()
        assert scheduler._scheduler._thread is thread
        assert [job.id for job in scheduler._scheduler.get_jobs()] == [OPTIMIZATION_JOB_ID]
    assert scheduler.running is False
    assert thread.is_alive() is False

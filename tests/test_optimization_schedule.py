"""The nightly job uses the same start function as Run now."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import recent_runs, settings_from
from deepfix_rag.optimization_schedule import (
    DEFAULT_CRON,
    DEFAULT_TIMEZONE,
    InvalidSchedule,
    OptimizationScheduler,
    compile_cron,
    cron_matches,
    next_run,
)

pytestmark = pytest.mark.integration


def test_the_default_fire_time_is_two_am_in_berlin() -> None:
    zone = ZoneInfo("Europe/Berlin")
    after = datetime(2026, 10, 12, 12, 0, tzinfo=zone)
    local = next_run(DEFAULT_CRON, after=after, timezone_name=DEFAULT_TIMEZONE).astimezone(zone)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 10, 13, 2, 0)


def test_sunday_matches_zero_and_seven() -> None:
    sunday = datetime(2026, 10, 11, 0, 0)
    monday = datetime(2026, 10, 12, 0, 0)
    assert sunday.weekday() == 6
    assert cron_matches(compile_cron("0 0 * * 0"), sunday)
    assert cron_matches(compile_cron("0 0 * * 7"), sunday)
    assert cron_matches(compile_cron("0 0 * * 0"), monday) is False


def test_an_invalid_expression_is_rejected() -> None:
    with pytest.raises(InvalidSchedule, match="five cron fields"):
        compile_cron("60 2 * * *")
    with pytest.raises(InvalidSchedule, match="five cron fields"):
        compile_cron("* * *")


def test_a_due_schedule_starts_the_same_job_and_a_pause_stops_it(tmp_path: Path) -> None:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    settings = Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
    )
    scheduler = OptimizationScheduler(
        settings.product_db_path,
        settings.workspace_dir,
        options_for=lambda: settings_from(settings),
        coordinator_for=lambda: None,
    )
    scheduler.start()
    try:
        moment = datetime(2026, 10, 12, 15, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        scheduler.update(f"{moment.minute} {moment.hour} * * *", True)
        assert scheduler.run_due(moment) is True
        assert scheduler.run_due(moment) is False
        runs = recent_runs(settings.product_db_path)
        assert runs[0].trigger == "cron"
        assert runs[0].status == "skipped"
        scheduler.update(scheduler.view().cron, False)
        assert scheduler.view().next_run_at is None
        assert scheduler.run_due(moment + timedelta(days=1)) is False
    finally:
        scheduler.stop()


def test_schedule_edits_survive_a_restart_and_reject_a_bad_cron(tmp_path: Path) -> None:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    settings = Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        ALLOWED_EMAIL_DOMAINS="example.com",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
    )
    with TestClient(create_app(settings)) as client:
        registered = client.post(
            "/auth/register",
            json={"email": "ada@example.com", "password": "correct-horse"},
        )
        assert registered.status_code == 201
        logged_in = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "correct-horse"},
        )
        assert logged_in.status_code == 200
        forbidden = client.get("/admin/optimization")
        assert forbidden.status_code == 403
        client.post("/auth/logout")
        _admin(client, settings, create=True)
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["cron"] == "0 2 * * *"
        assert body["timezone"] == "Europe/Berlin"
        assert body["enabled"] is True
        assert body["next_run_at"] is not None
        assert body["agent_available"] is False
        assert body["champion_config_path"].endswith("configs/champion.yaml")
        assert body["runs"] == []

        rejected = client.patch(
            "/admin/optimization",
            json={"cron": "not a cron", "enabled": False},
        )
        assert rejected.status_code == 400
        assert "five cron fields" in rejected.json()["detail"]
        assert client.get("/admin/optimization").json()["cron"] == "0 2 * * *"
        assert client.get("/admin/optimization").json()["enabled"] is True

        saved = client.patch(
            "/admin/optimization",
            json={"cron": "15 3 * * *", "enabled": False},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["cron"] == "15 3 * * *"
        assert saved.json()["enabled"] is False
        assert saved.json()["next_run_at"] is None

        paused = client.post("/admin/optimization/run", json={})
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "skipped"
        assert paused.json()["trigger"] == "manual"

    with TestClient(create_app(settings)) as restarted:
        _admin(restarted, settings, create=False)
        again = restarted.get("/admin/optimization")
        assert again.status_code == 200
        assert again.json()["cron"] == "15 3 * * *"
        assert again.json()["enabled"] is False
        assert again.json()["runs"][0]["trigger"] == "manual"
        assert again.json()["runs"][0]["status"] == "skipped"


def _admin(client: TestClient, settings: Settings, *, create: bool) -> None:
    from deepfix_rag.accounts import create_admin

    if create:
        create_admin(settings.product_db_path, "admin@example.com", "correct-horse")
    assert (
        client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "correct-horse"},
        ).status_code
        == 200
    )

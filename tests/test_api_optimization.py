"""The admin route starts the shared optimization job."""

from datetime import datetime, timedelta, timezone
import os
import subprocess

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import (
    CANCELLED_REASON,
    INTERRUPTED_REASON,
    STALE_REASON,
    init_optimization,
)
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def settings(tmp_path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
    )


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(create_app(settings, token_verifier=identity.verifier()))


def test_members_cannot_start_a_run_and_admins_see_a_skip(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with _client(settings, identity) as client:
        assert client.post("/admin/optimization/run", json={}).status_code == 401
        identity.sign_in(client, "ada@example.com")
        forbidden = client.post("/admin/optimization/run", json={})
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "Only admins can start an optimization run."

        identity.sign_in(client, "admin@example.com", "admin")
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "skipped"
        assert body["trigger"] == "manual"
        assert body["dev_count"] == 0
        assert "0 labeled questions in dev, 50 needed" in body["reason"]


def test_a_running_job_returns_conflict(settings: Settings, identity: IdentityIssuer) -> None:
    with _client(settings, identity) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        init_optimization(settings.product_db_path)
        with connect(settings.product_db_path) as conn:
            conn.execute(
                """
                INSERT INTO optimization_runs (
                    run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                    holdout_count, dev_query_ids, started_at
                )
                VALUES ('active', 'running', 'manual', 'snap', 0, 0, 0, '[]', 't')
                """
            )
        conflict = client.post("/admin/optimization/run", json={})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "A run is already in progress."


def _insert_running(
    settings: Settings, run_id: str, started_at: str, *, pid: int | None = None
) -> None:
    init_optimization(settings.product_db_path)
    with connect(settings.product_db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, pid, started_at
            )
            VALUES (?, 'running', 'manual', 'snap', 0, 0, 0, '[]', ?, ?)
            """,
            (run_id, pid, started_at),
        )


def test_startup_fails_a_run_left_running(settings: Settings, identity: IdentityIssuer) -> None:
    _insert_running(
        settings,
        "orphan",
        datetime.now(timezone.utc).isoformat(),
        pid=-1,
    )
    with _client(settings, identity) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        run = status.json()["runs"][0]
        assert run["run_id"] == "orphan"
        assert run["status"] == "failed"
        assert run["reason"] == INTERRUPTED_REASON
        assert run["ended_at"] is not None
        with connect(settings.product_db_path) as conn:
            pid = conn.execute(
                "SELECT pid FROM optimization_runs WHERE run_id = 'orphan'"
            ).fetchone()["pid"]
        assert pid is None
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "skipped"


def test_status_fails_a_run_older_than_the_timeout(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with _client(settings, identity) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        _insert_running(
            settings,
            "stale",
            (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
        )
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        run = status.json()["runs"][0]
        assert run["status"] == "failed"
        assert run["reason"] == STALE_REASON
        assert run["ended_at"] is not None
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 200, started.text
        assert started.json()["run_id"] != "stale"


def test_status_keeps_a_run_that_is_inside_the_timeout(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with _client(settings, identity) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        _insert_running(
            settings,
            "active",
            (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        run = status.json()["runs"][0]
        assert run["status"] == "running"
        assert run["ended_at"] is None
        conflict = client.post("/admin/optimization/run", json={})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "A run is already in progress."


def test_missing_openhands_returns_service_unavailable(
    settings: Settings, identity: IdentityIssuer
) -> None:
    open_settings = settings.model_copy(
        update={
            "OPTIMIZATION_MIN_DEV_QUESTIONS": 0,
            "OPTIMIZATION_MIN_NEW_DEV_QUESTIONS": 0,
            "OPTIMIZATION_MIN_HOLDOUT_QUESTIONS": 0,
        }
    )
    with _client(open_settings, identity) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        unavailable = client.post("/admin/optimization/run", json={})
        assert unavailable.status_code == 503
        assert "not set up" in unavailable.json()["detail"]


def test_cancel_kills_only_the_openhands_process_group(
    settings: Settings, identity: IdentityIssuer
) -> None:
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    other = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        with _client(settings, identity) as client:
            identity.sign_in(client, "admin@example.com", "admin")
            _insert_running(
                settings,
                "active",
                datetime.now(timezone.utc).isoformat(),
                pid=process.pid,
            )
            cancelled = client.post("/admin/optimization/runs/active/cancel")
            assert cancelled.status_code == 200, cancelled.text
            body = cancelled.json()
            assert body["status"] == "failed"
            assert body["reason"] == CANCELLED_REASON
            process.wait(timeout=2)
            assert process.returncode is not None
            os.kill(other.pid, 0)
            again = client.post("/admin/optimization/runs/active/cancel")
            assert again.status_code == 409
            started = client.post("/admin/optimization/run", json={})
            assert started.status_code == 200, started.text
    finally:
        for proc in (process, other):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)

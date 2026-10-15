"""The admin route starts the shared optimization job."""

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.accounts import connect, create_admin
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def settings(tmp_path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        ALLOWED_EMAIL_DOMAINS="example.com",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
    )


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _admin(client: TestClient, settings: Settings) -> None:
    create_admin(settings.product_db_path, "admin@example.com", "correct-horse")
    assert (
        client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "correct-horse"},
        ).status_code
        == 200
    )


def test_members_cannot_start_a_run_and_admins_see_a_skip(settings: Settings) -> None:
    with _client(settings) as client:
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
        forbidden = client.post("/admin/optimization/run", json={})
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "Only admins can start an optimization run."

        client.post("/auth/logout")
        _admin(client, settings)
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "skipped"
        assert body["trigger"] == "manual"
        assert body["dev_count"] == 0
        assert "0 labeled questions in dev, 50 needed" in body["reason"]


def test_a_running_job_returns_conflict(settings: Settings) -> None:
    with _client(settings) as client:
        _admin(client, settings)
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


def test_missing_openhands_returns_service_unavailable(settings: Settings) -> None:
    open_settings = settings.model_copy(
        update={
            "OPTIMIZATION_MIN_DEV_QUESTIONS": 0,
            "OPTIMIZATION_MIN_NEW_DEV_QUESTIONS": 0,
            "OPTIMIZATION_MIN_HOLDOUT_QUESTIONS": 0,
        }
    )
    with _client(open_settings) as client:
        _admin(client, open_settings)
        unavailable = client.post("/admin/optimization/run", json={})
        assert unavailable.status_code == 503
        assert "not set up" in unavailable.json()["detail"]

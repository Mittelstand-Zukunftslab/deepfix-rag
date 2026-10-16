"""The admin route starts the shared optimization job."""

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization
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

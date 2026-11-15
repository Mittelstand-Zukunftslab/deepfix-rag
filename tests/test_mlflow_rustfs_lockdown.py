"""MLflow and RustFS are reachable only from the Compose network.

MLflow has no authentication and the RustFS console has no IAM policy in front
of it, so the guard is the absence of a published port plus an internal endpoint
in every caller. These tests read the real ``docker-compose.yml``, the real
``example.env``, and the real admin route rather than restating their values, so
a later edit that reopens a port or points a service at a public hostname fails
here.
"""

from pathlib import Path
import re

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization, mlflow_run_url
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
# The Compose service names that stand for "inside the network only".
INTERNAL_MLFLOW = "http://mlflow:5000"
INTERNAL_RUSTFS = "http://rustfs:9000"
LOCKED_SERVICES = ("mlflow", "rustfs")


def _compose() -> dict:
    import yaml

    stack = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    included = yaml.safe_load((REPO_ROOT / "compose.hermes.yml").read_text(encoding="utf-8"))
    services = dict(stack["services"])
    services.update(included["services"])
    return {"services": services, "volumes": stack["volumes"]}


def _default_of(raw: object) -> str:
    """The fallback in a ``${NAME:-fallback}`` interpolation, or the literal value."""
    if not isinstance(raw, str):
        return ""
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", raw.strip())
    return match.group(1) if match else raw.strip()


def test_compose_publishes_no_port_for_mlflow_or_rustfs() -> None:
    services = _compose()["services"]
    for name in LOCKED_SERVICES:
        assert "ports" not in services[name], f"{name} must not publish a host port"


def test_the_rustfs_console_is_off_by_default() -> None:
    rustfs = _compose()["services"]["rustfs"]["environment"]
    assert rustfs["RUSTFS_CONSOLE_ENABLE"] == "${RUSTFS_CONSOLE_ENABLE:-false}"
    assert _default_of(rustfs["RUSTFS_CONSOLE_ENABLE"]) == "false"


def test_every_caller_reaches_mlflow_and_rustfs_over_the_compose_network() -> None:
    composition = _compose()
    services = composition["services"]
    assert set(LOCKED_SERVICES) <= set(services)
    callers = 0
    for name, service in services.items():
        environment = service.get("environment") or {}
        for key, expected in (
            ("MLFLOW_TRACKING_URI", INTERNAL_MLFLOW),
            ("S3_ENDPOINT_URL", INTERNAL_RUSTFS),
        ):
            if key not in environment:
                continue
            callers += 1
            assert _default_of(environment[key]) == expected, f"{name}.{key}"
    # backend-api, hermes and diagnostic-agent each read one of the two.
    assert callers >= 3


def test_example_env_keeps_both_endpoints_internal() -> None:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / "example.env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value.strip()
    assert values["MLFLOW_TRACKING_URI"] == INTERNAL_MLFLOW
    assert values["S3_ENDPOINT_URL"] == INTERNAL_RUSTFS
    assert values["RUSTFS_CONSOLE_ENABLE"] == "false"


def test_mlflow_run_url_is_none_for_an_internal_tracking_uri() -> None:
    """An internal tracking URI has no page a browser can open, so no link is built."""
    previous = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri(INTERNAL_MLFLOW)
        assert mlflow_run_url("some-run-id") is None
    finally:
        mlflow.set_tracking_uri(previous)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def test_the_admin_screen_keeps_the_run_but_drops_the_mlflow_link(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """The run and its MLflow id survive; only the unusable link is withheld."""
    settings = _settings(tmp_path)
    init_optimization(settings.product_db_path)
    with connect(settings.product_db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, mlflow_run_id, started_at, ended_at
            )
            VALUES ('locked', 'succeeded', 'manual', 'snap', 0, 0, 0, '[]',
                    'mlflow-eval-run-1', 't', 't')
            """
        )
    previous = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri(INTERNAL_MLFLOW)
        with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
            identity.sign_in(client, "admin@example.com", "admin")
            status = client.get("/admin/optimization")
            assert status.status_code == 200, status.text
            run = status.json()["runs"][0]
            assert run["run_id"] == "locked"
            assert run["mlflow_run_id"] == "mlflow-eval-run-1"
            assert run["mlflow_url"] is None
    finally:
        mlflow.set_tracking_uri(previous)

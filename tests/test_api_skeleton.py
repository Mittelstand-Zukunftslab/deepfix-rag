"""Contract tests for the FastAPI shell and its exported OpenAPI document."""

import json
from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from deepfix_rag.api.app import app, create_app
from deepfix_rag.api.export_openapi import DEFAULT_OUTPUT, export_openapi
from deepfix_rag.config.settings import Settings

pytestmark = [pytest.mark.api, pytest.mark.unit]

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _operations(document: dict) -> list[tuple[str, str, dict]]:
    return [
        (path, method, operation)
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def test_health_returns_ok_without_backends(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_served_and_lists_health(client: TestClient):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/health" in response.json()["paths"]


def test_openapi_has_no_internal_paths(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/internal")]


def test_every_public_route_has_explicit_snake_case_operation_id():
    operations = _operations(app.openapi())
    assert operations
    for path, method, operation in operations:
        operation_id = operation.get("operationId", "")
        assert SNAKE_CASE.match(operation_id), f"{method.upper()} {path}: {operation_id!r}"
        assert not operation_id.endswith(f"_{method}"), (
            f"{method.upper()} {path} uses an auto-generated operation id: {operation_id!r}"
        )


def test_every_public_route_declares_a_typed_success_response():
    for path, method, operation in _operations(app.openapi()):
        success = {code: body for code, body in operation["responses"].items() if code[0] == "2"}
        assert success, f"{method.upper()} {path} has no 2xx response"
        for code, body in success.items():
            if code == "204":
                assert "content" not in body, f"{method.upper()} {path} 204 must not include a body"
                continue
            assert body.get("content", {}).get("application/json", {}).get("schema"), (
                f"{method.upper()} {path} {code} has no response schema"
            )


def test_cors_allows_only_the_configured_web_origin():
    web_origin = "https://app.example.test"
    cors_client = TestClient(create_app(Settings(WEB_ORIGIN=web_origin)))
    preflight_headers = {"Access-Control-Request-Method": "GET"}

    allowed = cors_client.options("/health", headers={"Origin": web_origin, **preflight_headers})
    assert allowed.headers["access-control-allow-origin"] == web_origin
    assert allowed.headers["access-control-allow-credentials"] == "true"

    denied = cors_client.options(
        "/health", headers={"Origin": "https://evil.example.test", **preflight_headers}
    )
    assert "access-control-allow-origin" not in denied.headers


def test_export_openapi_writes_the_app_schema(tmp_path: Path):
    target = export_openapi(tmp_path / "nested" / "openapi.json")
    assert json.loads(target.read_text(encoding="utf-8")) == app.openapi()


def test_committed_openapi_matches_the_app():
    assert DEFAULT_OUTPUT.exists(), "Run `uv run python -m deepfix_rag.api.export_openapi`."
    committed = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert committed == app.openapi(), "web/openapi.json is stale; re-run `npm run generate:api`."

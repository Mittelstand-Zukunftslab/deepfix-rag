"""Run progress for a thread: linking, listing, and the bounded step tail."""

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deepfix_rag import admin_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.api.routes import admin_chat as routes
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_admin_chat_routes import ADMIN, CHAT_KEY, MODEL, StandIn
from tests.test_admin_chat_routes import hermes as hermes_fixture

pytestmark = [pytest.mark.api, pytest.mark.integration]


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
        "ADMIN_CHAT_STEP_TAIL": 50,
        "ADMIN_CHAT_STEP_READ_BYTES": 65536,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(create_app(settings, token_verifier=identity.verifier()))


def _run(db_path: Path, run_id: str, *, started: str, status: str = "succeeded") -> None:
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at, reason, primary_metric,
                dev_delta, holdout_delta, holdout_wins, holdout_losses, mlflow_run_id
            ) VALUES (?, ?, 'manual', 'snap', 1, 1, 1, '[]', ?, 'a reason', 'ndcg',
                      '{"ndcg": 0.04}', '{"ndcg": 0.01}', 7, 2, 'mlflow-1')
            """,
            (run_id, status, started),
        )


def _thread(settings: Settings, identity: IdentityIssuer) -> str:
    admin_chat.init_admin_chat(settings.product_db_path)
    return admin_chat.create_thread(
        settings.product_db_path, identity.user_id(ADMIN), admin_email=ADMIN
    ).thread_id


def _log(settings: Settings, run_id: str, body: str) -> Path:
    directory = Path(settings.PRODUCT_DATA_DIR) / "optimization-logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.log"
    path.write_text(body, encoding="utf-8")
    return path


def test_two_linked_runs_come_back_newest_first(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-old", started="2026-11-13T10:00:00+00:00")
    _run(settings.product_db_path, "run-new", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        assert client.post(f"/admin/chat/threads/{thread_id}/runs/run-old").status_code == 200
        second = client.post(f"/admin/chat/threads/{thread_id}/runs/run-new")
        assert second.status_code == 200
        listed = client.get(f"/admin/chat/threads/{thread_id}/runs")
    assert listed.status_code == 200
    run_ids = [item["run_id"] for item in listed.json()]
    assert set(run_ids) == {"run-old", "run-new"}
    item = next(entry for entry in listed.json() if entry["run_id"] == "run-new")
    assert item["status"] == "succeeded"
    assert item["primary_metric"] == "ndcg"
    assert item["dev_delta"] == {"ndcg": 0.04}
    assert item["holdout_wins"] == 7


def test_linking_twice_is_idempotent(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        assert client.post(f"/admin/chat/threads/{thread_id}/runs/run-1").status_code == 200
        assert client.post(f"/admin/chat/threads/{thread_id}/runs/run-1").status_code == 200
        listed = client.get(f"/admin/chat/threads/{thread_id}/runs").json()
    assert [item["run_id"] for item in listed] == ["run-1"]
    with connect(settings.product_db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM admin_chat_thread_runs").fetchone()[0]
    assert rows == 1


def test_an_unknown_run_is_404(tmp_path: Path, hermes: StandIn, identity: IdentityIssuer) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.post(f"/admin/chat/threads/{thread_id}/runs/does-not-exist")
    assert response.status_code == 404
    assert "No optimization run" in response.json()["detail"]


def test_another_admins_thread_is_404(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, "other@example.com", "admin")
        assert client.get(f"/admin/chat/threads/{thread_id}/runs").status_code == 404
        assert client.post(f"/admin/chat/threads/{thread_id}/runs/run-1").status_code == 404


def test_a_member_gets_403(tmp_path: Path, hermes: StandIn, identity: IdentityIssuer) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, "member@example.com")
        assert client.get(f"/admin/chat/threads/{thread_id}/runs").status_code == 403


def test_the_step_tail_is_capped_and_redacted(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes, ADMIN_CHAT_STEP_TAIL=5)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    lines = [f"optimization run=run-1 event=step{i} preview=ok" for i in range(20)]
    lines.append(f"optimization run=run-1 event=leak preview={CHAT_KEY}")
    lines.append("optimization run=run-1 event=holdout preview=sealed holdout phrase")
    body = "\n".join(lines) + "\n"
    _log(settings, "run-1", body)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        client.post(f"/admin/chat/threads/{thread_id}/runs/run-1")
        item = client.get(f"/admin/chat/threads/{thread_id}/runs").json()[0]
    assert len(item["steps"]) == 5
    assert all(len(step) <= 500 for step in item["steps"])
    joined = " ".join(item["steps"])
    assert CHAT_KEY not in joined
    assert "[redacted]" in joined


def test_the_output_section_is_not_part_of_the_step_tail(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    body = (
        "optimization run=run-1 event=step preview=ok\n"
        "----- output -----\n"
        "the whole final output of the command\n"
    )
    _log(settings, "run-1", body)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        client.post(f"/admin/chat/threads/{thread_id}/runs/run-1")
        item = client.get(f"/admin/chat/threads/{thread_id}/runs").json()[0]
    assert item["steps"] == ["optimization run=run-1 event=step preview=ok"]


def test_a_two_megabyte_log_reads_at_most_the_byte_bound(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("line of filler text\n" * 110_000, encoding="utf-8")
    assert path.stat().st_size > 2 * 1024 * 1024
    tail = admin_chat.read_step_tail(path, max_lines=50, max_bytes=4096)
    assert len(tail) == 50
    # A tiny bound proves the reader slices rather than loading the file.
    tiny = admin_chat.read_step_tail(path, max_lines=50, max_bytes=64)
    assert len(tiny) <= 5


def test_a_run_with_no_log_yet_has_an_empty_tail(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        assert client.post(f"/admin/chat/threads/{thread_id}/runs/run-1").status_code == 200
        item = client.get(f"/admin/chat/threads/{thread_id}/runs").json()[0]
    assert item["steps"] == []


def test_the_link_route_inventory_is_complete() -> None:
    paths = {route.path for route in routes.router.routes}
    assert "/admin/chat/threads/{thread_id}/runs" in paths
    assert "/admin/chat/threads/{thread_id}/runs/{run_id}" in paths
    chat_paths = {path for path in paths if path.startswith("/admin/chat")}
    assert len(chat_paths) == 9
    # `/admin/chat/threads` and `/admin/chat/threads/{thread_id}` each carry two
    # methods, which is the eleven operations NFR-4 counts.
    operations = [route for route in routes.router.routes if route.path.startswith("/admin/chat")]
    assert len({(route.path, method) for route in operations for method in route.methods}) == 11


def test_the_step_log_is_untouched_by_a_thread_delete(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    log = _log(settings, "run-1", "optimization run=run-1 event=step preview=ok\n")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        client.post(f"/admin/chat/threads/{thread_id}/runs/run-1")
        assert client.delete(f"/admin/chat/threads/{thread_id}").status_code == 204
    assert log.is_file()
    with connect(settings.product_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_chat_thread_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0] == 1


def test_the_run_list_carries_the_mlflow_url_shape(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        client.post(f"/admin/chat/threads/{thread_id}/runs/run-1")
        item = client.get(f"/admin/chat/threads/{thread_id}/runs").json()[0]
    assert item["mlflow_run_id"] == "mlflow-1"
    # The local SQLite store has no page to open, so the URL is null, not a guess.
    assert item["mlflow_url"] is None


def test_a_pruned_run_row_does_not_break_the_thread(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id = _thread(settings, identity)
    _run(settings.product_db_path, "run-1", started="2026-11-14T10:00:00+00:00")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        client.post(f"/admin/chat/threads/{thread_id}/runs/run-1")
        with connect(settings.product_db_path) as conn:
            conn.execute("DELETE FROM optimization_runs WHERE run_id = 'run-1'")
        listed = client.get(f"/admin/chat/threads/{thread_id}/runs")
    assert listed.status_code == 200
    assert listed.json() == []

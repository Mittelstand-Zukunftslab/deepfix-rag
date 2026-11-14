"""Threads, resume, cancel, retry, and restart recovery.

The resume tail needs a live turn, which means a real server; the restart case
is a fresh lifespan over the same database. Both use the Hermes stand-in from
``tests/test_admin_chat_routes.py`` rather than a second one.
"""

from collections.abc import Iterator
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deepfix_rag import admin_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.api.routes import admin_chat as routes
from deepfix_rag.config.settings import Settings
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_admin_chat_routes import ADMIN, CHAT_KEY, MODEL, StandIn
from tests.test_admin_chat_routes import hermes as hermes_fixture

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def hermes() -> Iterator[StandIn]:
    yield from hermes_fixture.__wrapped__()  # type: ignore[attr-defined]


def _settings(tmp_path: Path, hermes: StandIn) -> Settings:
    return Settings(
        _env_file=None,
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        HERMES_CHAT_ENABLED=True,
        HERMES_CHAT_BASE_URL=hermes.url,
        HERMES_CHAT_API_SERVER_KEY=CHAT_KEY,
        HERMES_CHAT_MODEL="",
        HERMES_MODEL=MODEL,
        ADMIN_CHAT_MAX_PROMPT_CHARS=200,
        ADMIN_CHAT_MAX_MESSAGES=10,
    )


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(create_app(settings, token_verifier=identity.verifier()))


def _frames(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
    ]


def _thread(client: TestClient, identity: IdentityIssuer) -> str:
    identity.sign_in(client, ADMIN, "admin")
    return client.post("/admin/chat/threads").json()["thread_id"]


def _conversation(client: TestClient, thread_id: str, content: str) -> list[dict]:
    stored = client.get(f"/admin/chat/threads/{thread_id}").json()["messages"]
    return [{"role": m["role"], "content": m["content"]} for m in stored] + [
        {"role": "user", "content": content}
    ]


def _send(client: TestClient, thread_id: str, content: str, **extra: object) -> None:
    response = client.post(
        "/admin/chat/completions",
        json={
            "thread_id": thread_id,
            "messages": _conversation(client, thread_id, content),
            **extra,
        },
    )
    assert response.status_code == 200, response.text


def test_the_thread_list_carries_its_last_turn_status(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        thread_id = _thread(client, identity)
        _send(client, thread_id, "Hallo")
        listed = client.get("/admin/chat/threads").json()
    assert [item["thread_id"] for item in listed] == [thread_id]
    assert listed[0]["title"] == "Hallo"
    assert listed[0]["last_turn_status"] == "completed"


def test_a_thread_detail_has_messages_turns_and_actions(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        thread_id = _thread(client, identity)
        _send(client, thread_id, "Hallo")
        detail = client.get(f"/admin/chat/threads/{thread_id}").json()
    assert detail["thread"]["thread_id"] == thread_id
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert [t["status"] for t in detail["turns"]] == ["completed"]
    assert detail["actions"] == []


def test_a_resume_after_completion_replays_the_stored_text_once(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        thread_id = _thread(client, identity)
        _send(client, thread_id, "Hallo")
        turn_id = client.get(f"/admin/chat/threads/{thread_id}").json()["turns"][-1]["turn_id"]
        resumed = client.get(f"/admin/chat/turns/{turn_id}/stream")
        assert resumed.status_code == 200
        assert resumed.headers["x-vercel-ai-ui-message-stream"] == "v1"
        frames = _frames(resumed.text)
    text = "".join(f["delta"] for f in frames if f.get("type") == "text-delta")
    assert text == "Das ist eine Antwort."
    turn = next(f for f in frames if f["type"] == "data-deepfix-turn")
    assert turn["data"]["status"] == "completed"


def test_a_resume_of_another_admins_turn_is_404(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    admin_chat.init_admin_chat(settings.product_db_path)
    other = admin_chat.create_thread(settings.product_db_path, identity.user_id("x@example.com"))
    turn = admin_chat.start_turn(settings.product_db_path, other.thread_id, "other")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        assert client.get(f"/admin/chat/turns/{turn.turn_id}/stream").status_code == 404
        assert client.get("/admin/chat/turns/nope/stream").status_code == 404


def test_a_restart_marks_the_turn_failed_and_the_resume_reports_it(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    admin_chat.init_admin_chat(settings.product_db_path)
    thread = admin_chat.create_thread(
        settings.product_db_path, identity.user_id(ADMIN), admin_email=ADMIN
    )
    turn = admin_chat.start_turn(
        settings.product_db_path, thread.thread_id, identity.user_id(ADMIN)
    )
    admin_chat.append_message(
        settings.product_db_path,
        thread.thread_id,
        role="assistant",
        content="partial",
        turn_id=turn.turn_id,
    )
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        frames = _frames(client.get(f"/admin/chat/turns/{turn.turn_id}/stream").text)
    settled = admin_chat.get_turn(settings.product_db_path, turn.turn_id)
    assert settled.status == "failed"
    assert settled.error == admin_chat.INTERRUPTED
    text = "".join(f["delta"] for f in frames if f.get("type") == "text-delta")
    assert text == "partial"
    terminal = next(f for f in frames if f["type"] == "data-deepfix-turn")
    assert terminal["data"]["status"] == "failed"


def test_a_retry_of_a_turn_in_another_thread_is_400(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    admin_chat.init_admin_chat(settings.product_db_path)
    admin_id = identity.user_id(ADMIN)
    mine = admin_chat.create_thread(settings.product_db_path, admin_id, admin_email=ADMIN)
    other = admin_chat.create_thread(settings.product_db_path, admin_id, admin_email=ADMIN)
    foreign = admin_chat.start_turn(settings.product_db_path, other.thread_id, admin_id)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.post(
            "/admin/chat/completions",
            json={
                "thread_id": mine.thread_id,
                "messages": [{"role": "user", "content": "hi"}],
                "retry_of": foreign.turn_id,
            },
        )
    assert response.status_code == 400
    assert "not in this thread" in response.json()["detail"]


def test_a_delete_removes_messages_and_leaves_the_audit_rows(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        thread_id = _thread(client, identity)
        _send(client, thread_id, "Hallo")
        assert client.delete(f"/admin/chat/threads/{thread_id}").status_code == 204
    with connect(settings.product_db_path) as conn:
        messages = conn.execute("SELECT COUNT(*) FROM admin_chat_messages").fetchone()[0]
        audits = conn.execute("SELECT kind FROM admin_chat_audit ORDER BY event_id").fetchall()
    assert messages == 0
    kinds = [str(row["kind"]) for row in audits]
    assert kinds == ["turn_started", "turn_completed", "thread_deleted"]


def test_gate_and_configuration_checks_come_after_identity_and_role(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    """An unsigned caller is 401 and a member 403 even on a disabled deployment."""
    settings = _settings(tmp_path, hermes).model_copy(
        update={"HERMES_CHAT_ENABLED": False, "HERMES_CHAT_API_SERVER_KEY": None}
    )
    with _client(settings, identity) as client:
        assert client.get("/admin/chat/threads").status_code == 401
        identity.sign_in(client, "member@example.com")
        assert client.get("/admin/chat/threads").status_code == 403
        identity.sign_in(client, ADMIN, "admin")
        disabled = client.get("/admin/chat/threads")
        assert disabled.status_code == 503
        assert disabled.json()["detail"]["code"] == routes.DISABLED_CODE


def test_retry_creates_a_linked_turn_and_streams_a_fresh_reply(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        thread_id = _thread(client, identity)
        _send(client, thread_id, "Hallo")
        detail = client.get(f"/admin/chat/threads/{thread_id}").json()
        first = detail["turns"][-1]["turn_id"]
        # A retry resends the same user message: the client array drops the
        # stored answer for that turn and names the turn it replaces.
        retry_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in detail["messages"]
            if not (m["role"] == "assistant" and m["turn_id"] == first)
        ]
        response = client.post(
            "/admin/chat/completions",
            json={"thread_id": thread_id, "messages": retry_messages, "retry_of": first},
        )
        assert response.status_code == 200, response.text
        turns = client.get(f"/admin/chat/threads/{thread_id}").json()["turns"]
    assert turns[-1]["retry_of"] == first
    assert turns[-1]["status"] == "completed"
    assert len(turns) == 2


def test_every_chat_route_is_gated_or_an_exempt_constant() -> None:
    """Later stories grow this router; every new path must be gated or exempt."""
    chat_paths = {route.path for route in routes.router.routes}
    assert chat_paths == {
        "/admin/chat/completions",
        "/admin/chat/turns/{turn_id}/stream",
        "/admin/chat/threads",
        "/admin/chat/threads/{thread_id}",
    }
    # The two exempt paths are constants, and the delete route is one of them.
    assert set(routes.GATE_EXEMPT_PATHS) == {
        "/admin/chat/audit",
        "/admin/chat/threads/{thread_id}",
    }
    assert "/admin/chat/threads/{thread_id}" in chat_paths

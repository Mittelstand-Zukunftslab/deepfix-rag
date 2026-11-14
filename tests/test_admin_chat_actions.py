"""Action cards: parsing, storing, confirming, rejecting, and the safety walk."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deepfix_rag import admin_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.api.routes import admin_chat as routes
from deepfix_rag.api.routes import optimization as optimization_routes
from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import init_optimization
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_admin_chat_routes import ADMIN, CHAT_KEY, MODEL, StandIn
from tests.test_admin_chat_routes import hermes as hermes_fixture

pytestmark = [pytest.mark.api, pytest.mark.integration]

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


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
        "ADMIN_CHAT_MAX_PROMPT_CHARS": 4000,
        "ADMIN_CHAT_MAX_MESSAGES": 20,
        "ADMIN_CHAT_ACTION_TTL_SECONDS": 600,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(create_app(settings, token_verifier=identity.verifier()))


def _block(kind: str, reason: str = "because", **extra: object) -> str:
    payload = {"kind": kind, "reason": reason, **extra}
    return f"```deepfix-action\n{json.dumps(payload)}\n```"


def _parsed(text: str, db_path: Path | None = None) -> admin_chat.ParseResult:
    return admin_chat.parse_actions(text, db_path=db_path)


def test_a_valid_block_becomes_one_card() -> None:
    parsed = _parsed(f"Here is why.\n{_block('start_optimization_run')}")
    assert [action.kind for action in parsed.actions] == ["start_optimization_run"]
    assert parsed.dropped == ()


def test_an_unknown_kind_is_dropped_and_counted() -> None:
    parsed = _parsed(_block("apply_candidate"))
    assert parsed.actions == ()
    assert "unknown_kind:apply_candidate" in parsed.dropped


def test_a_malformed_block_is_dropped_and_counted() -> None:
    parsed = _parsed("```deepfix-action\nnot json\n```")
    assert parsed.actions == ()
    assert parsed.dropped == ("malformed",)


def test_at_most_two_blocks_are_kept() -> None:
    text = "\n".join(_block("start_optimization_run", f"reason {i}") for i in range(4))
    parsed = _parsed(text)
    assert len(parsed.actions) == 2
    assert parsed.dropped == ("extra", "extra")


def test_a_cancel_block_needs_a_run_that_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "product.db"
    init_optimization(db_path)
    missing = _parsed(_block("cancel_optimization_run", run_id="nope"), db_path)
    assert missing.actions == ()
    assert missing.dropped == ("unknown_run",)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO optimization_runs (run_id, status, trigger_kind, snapshot_id,"
            " dev_count, new_dev_count, holdout_count, dev_query_ids, started_at)"
            " VALUES ('run-1', 'running', 'manual', 'snap', 0, 0, 0, '[]', 't')"
        )
    present = _parsed(_block("cancel_optimization_run", run_id="run-1"), db_path)
    assert [action.kind for action in present.actions] == ["cancel_optimization_run"]
    assert present.actions[0].payload["run_id"] == "run-1"


def test_a_cancel_block_without_a_run_id_is_dropped() -> None:
    parsed = _parsed(_block("cancel_optimization_run"))
    assert parsed.actions == ()
    assert parsed.dropped == ("missing_run_id",)


def test_stored_cards_are_proposed_and_expire_by_ttl(tmp_path: Path) -> None:
    db_path = tmp_path / "product.db"
    admin_chat.init_admin_chat(db_path)
    thread = admin_chat.create_thread(db_path, "admin-1")
    turn = admin_chat.start_turn(db_path, thread.thread_id, "admin-1")
    stored = admin_chat.store_actions(
        db_path,
        thread_id=thread.thread_id,
        turn_id=turn.turn_id,
        parsed=_parsed(_block("start_optimization_run")),
        ttl_seconds=600,
        now=NOW,
    )
    assert len(stored) == 1
    card = stored[0]
    assert card.status == "proposed"
    assert card.expires_at == (NOW + timedelta(seconds=600)).isoformat()
    # Still live inside the window, expired after it, and decided exactly once.
    assert admin_chat.expire_if_due(db_path, card.action_id, now=NOW).status == "proposed"
    expired = admin_chat.expire_if_due(db_path, card.action_id, now=NOW + timedelta(seconds=601))
    assert expired.status == "expired"
    assert (
        admin_chat.decide_action(db_path, card.action_id, status="confirmed", admin_id="admin-1")
        is False
    )


def test_the_policy_example_block_parses() -> None:
    """CHAT-004 quotes the shape; this fails if the two drift apart."""
    prompt = admin_chat.system_prompt(
        Settings(_env_file=None, PRODUCT_DATA_DIR=Path("/tmp/x"), WORKSPACE_DIR=Path("/tmp/y"))
    )
    start = prompt.index("```deepfix-action")
    end = prompt.index("```", start + 3) + 3
    parsed = _parsed(prompt[start:end])
    assert [action.kind for action in parsed.actions] == ["start_optimization_run"]


def _thread_with_card(
    settings: Settings, identity: IdentityIssuer, *, reply: str | None = None
) -> tuple[str, str]:
    admin_id = identity.user_id(ADMIN)
    admin_chat.init_admin_chat(settings.product_db_path)
    thread = admin_chat.create_thread(settings.product_db_path, admin_id, admin_email=ADMIN)
    turn = admin_chat.start_turn(settings.product_db_path, thread.thread_id, admin_id)
    admin_chat.append_message(
        settings.product_db_path,
        thread.thread_id,
        role="assistant",
        content=reply or _block("start_optimization_run", "the gate looks ready"),
        turn_id=turn.turn_id,
    )
    admin_chat.finish_turn(settings.product_db_path, turn.turn_id, status="completed")
    stored = admin_chat.store_actions(
        settings.product_db_path,
        thread_id=thread.thread_id,
        turn_id=turn.turn_id,
        parsed=_parsed(reply or _block("start_optimization_run", "the gate looks ready")),
        ttl_seconds=settings.ADMIN_CHAT_ACTION_TTL_SECONDS,
    )
    return thread.thread_id, stored[0].action_id


def test_confirming_a_start_returns_the_optimization_response(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id, action_id = _thread_with_card(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.post(f"/admin/chat/actions/{action_id}/confirm")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "skipped"
        assert body["trigger"] == "manual"
        thread = client.get(f"/admin/chat/threads/{thread_id}").json()
    assert thread["actions"][0]["status"] == "confirmed"
    kinds = [row["kind"] for row in _audit(settings)]
    assert "action_confirmed" in kinds


def _audit(settings: Settings) -> list[dict]:
    return [
        {"kind": entry.kind, "detail": entry.detail}
        for entry in admin_chat.list_audit(settings.product_db_path, limit=100)
    ]


def test_a_second_confirm_is_409(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    _, action_id = _thread_with_card(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        assert client.post(f"/admin/chat/actions/{action_id}/confirm").status_code == 200
        again = client.post(f"/admin/chat/actions/{action_id}/confirm")
    assert again.status_code == 409
    assert "already been decided" in again.json()["detail"]


def test_an_expired_card_is_409_and_audited(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes, ADMIN_CHAT_ACTION_TTL_SECONDS=1)
    _, action_id = _thread_with_card(settings, identity)
    with connect(settings.product_db_path) as conn:
        conn.execute("UPDATE admin_chat_actions SET expires_at = '2000-01-01T00:00:00+00:00'")
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.post(f"/admin/chat/actions/{action_id}/confirm")
    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    assert "action_expired" in [row["kind"] for row in _audit(settings)]


def test_another_admins_card_is_404(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    _, action_id = _thread_with_card(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, "other@example.com", "admin")
        assert client.post(f"/admin/chat/actions/{action_id}/confirm").status_code == 404
        assert client.post(f"/admin/chat/actions/{action_id}/reject").status_code == 404


def test_a_member_cannot_confirm(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    _, action_id = _thread_with_card(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, "member@example.com")
        assert client.post(f"/admin/chat/actions/{action_id}/confirm").status_code == 403


def test_a_running_run_makes_a_confirmed_start_409(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    thread_id, action_id = _thread_with_card(settings, identity)
    init_optimization(settings.product_db_path)
    with connect(settings.product_db_path) as conn:
        conn.execute(
            "INSERT INTO optimization_runs (run_id, status, trigger_kind, snapshot_id,"
            " dev_count, new_dev_count, holdout_count, dev_query_ids, started_at)"
            " VALUES ('active', 'running', 'manual', 'snap', 0, 0, 0, '[]', 't')"
        )
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        response = client.post(f"/admin/chat/actions/{action_id}/confirm")
        thread = client.get(f"/admin/chat/threads/{thread_id}").json()
    assert response.status_code == 409
    assert response.json()["detail"] == "A run is already in progress."
    # A retryable conflict leaves the card confirmable.
    assert thread["actions"][0]["status"] == "proposed"


def test_reject_marks_the_card_and_audits(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    _, action_id = _thread_with_card(settings, identity)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        rejected = client.post(f"/admin/chat/actions/{action_id}/reject")
        again = client.post(f"/admin/chat/actions/{action_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert again.status_code == 409
    assert "action_rejected" in [row["kind"] for row in _audit(settings)]


def test_a_reply_with_a_block_streams_the_card_in_the_terminal_frame(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    hermes.state.chunks = ["Take ", "this. ", _block("start_optimization_run", "ready")]
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        thread_id = client.post("/admin/chat/threads").json()["thread_id"]
        response = client.post(
            "/admin/chat/completions",
            json={"thread_id": thread_id, "messages": [{"role": "user", "content": "start?"}]},
        )
        assert response.status_code == 200, response.text
        frames = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]
        thread = client.get(f"/admin/chat/threads/{thread_id}").json()
    turn = next(f for f in frames if f["type"] == "data-deepfix-turn")
    assert len(turn["data"]["actions"]) == 1
    assert turn["data"]["actions"][0]["kind"] == "start_optimization_run"
    assert thread["actions"][0]["status"] == "proposed"


def test_a_reply_without_a_block_carries_no_actions(
    tmp_path: Path, hermes: StandIn, identity: IdentityIssuer
) -> None:
    settings = _settings(tmp_path, hermes)
    with _client(settings, identity) as client:
        identity.sign_in(client, ADMIN, "admin")
        thread_id = client.post("/admin/chat/threads").json()["thread_id"]
        response = client.post(
            "/admin/chat/completions",
            json={"thread_id": thread_id, "messages": [{"role": "user", "content": "hi"}]},
        )
        frames = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]
    turn = next(f for f in frames if f["type"] == "data-deepfix-turn")
    assert turn["data"]["actions"] == []


def test_no_chat_callable_can_promote_or_read_holdout() -> None:
    """The safety invariant, walked rather than reviewed."""
    import inspect

    source = inspect.getsource(admin_chat)
    for forbidden in ("apply_candidate", "champion.yaml", "is_holdout", "_holdout_records"):
        assert forbidden not in source, forbidden
    public = [
        name
        for name, value in vars(admin_chat).items()
        if not name.startswith("_") and inspect.isfunction(value)
    ]
    assert "parse_actions" in public
    assert not [name for name in public if "promote" in name]


def test_the_chat_confirm_reaches_the_same_helper_as_run_now() -> None:
    """A source assertion: neither route calls the domain function directly."""
    import inspect

    chat_source = inspect.getsource(routes)
    optimization_source = inspect.getsource(optimization_routes)
    assert "start_run_for(request)" in chat_source
    assert "start_run_for(request)" in optimization_source
    assert "start_optimization_run(" not in optimization_source.split("def start_run_for")[0]


def test_the_router_registers_the_two_action_routes() -> None:
    paths = {route.path for route in routes.router.routes}
    assert "/admin/chat/actions/{action_id}/confirm" in paths
    assert "/admin/chat/actions/{action_id}/reject" in paths

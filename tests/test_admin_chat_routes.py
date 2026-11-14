"""The chat router's gate: the disabled 503 and its two exempt paths.

CHAT-003 adds the streaming route and the "every route is gated or exempt" walk
to this module; this story pins the predicate on its own, because the routes it
guards do not exist yet.
"""

from pathlib import Path

from fastapi import HTTPException
import pytest

from deepfix_rag.api.routes import admin_chat
from deepfix_rag.config.settings import Settings

pytestmark = [pytest.mark.api, pytest.mark.unit]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values = {
        "PRODUCT_DATA_DIR": tmp_path / "private",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "WEB_ORIGIN": "http://localhost:8080",
        "HERMES_CHAT_ENABLED": False,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_chat_is_off_by_default_and_the_gate_says_so(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.HERMES_CHAT_ENABLED is False
    with pytest.raises(HTTPException) as raised:
        admin_chat.require_chat_enabled(settings)
    error = raised.value
    assert error.status_code == 503
    assert error.detail == {
        "code": "chat_disabled",
        "message": "Hermes chat is turned off on this server.",
    }
    assert admin_chat.DISABLED_CODE == "chat_disabled"
    assert admin_chat.DISABLED_MESSAGE == "Hermes chat is turned off on this server."


def test_the_two_sqlite_only_routes_are_the_only_exemptions() -> None:
    assert admin_chat.GATE_EXEMPT_PATHS == (
        "/admin/chat/audit",
        "/admin/chat/threads/{thread_id}",
    )


def test_enabled_chat_passes_the_gate(tmp_path: Path) -> None:
    admin_chat.require_chat_enabled(_settings(tmp_path, HERMES_CHAT_ENABLED=True))


def test_the_disabled_and_misconfigured_bodies_differ(tmp_path: Path) -> None:
    disabled = _settings(tmp_path)
    misconfigured = _settings(tmp_path, HERMES_CHAT_ENABLED=True, HERMES_CHAT_MODEL="")
    with pytest.raises(HTTPException) as disabled_error:
        admin_chat.require_chat(disabled)
    with pytest.raises(HTTPException) as misconfigured_error:
        admin_chat.require_chat(misconfigured)
    assert disabled_error.value.detail["code"] == "chat_disabled"
    assert misconfigured_error.value.detail["code"] == "chat_misconfigured"
    assert disabled_error.value.detail["message"] != misconfigured_error.value.detail["message"]
    assert "HERMES_CHAT_API_SERVER_KEY" in misconfigured_error.value.detail["message"]
    assert "HERMES_CHAT_MODEL" in misconfigured_error.value.detail["message"]
    assert "HERMES_MODEL" in misconfigured_error.value.detail["message"]


def test_the_chat_model_falls_back_to_hermes_model(tmp_path: Path) -> None:
    settings = _settings(tmp_path, HERMES_CHAT_MODEL="", HERMES_MODEL="fixture/optimizer")
    assert admin_chat.chat_model(settings) == "fixture/optimizer"
    assert admin_chat.chat_model(_settings(tmp_path, HERMES_CHAT_MODEL="fixture/chat")) == (
        "fixture/chat"
    )

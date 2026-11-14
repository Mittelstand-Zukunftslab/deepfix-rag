"""Admin Hermes chat routes.

This module starts with the router-level gate only. CHAT-003 adds the streaming
completion route, and CHAT-005 through CHAT-008 add the thread, action, run, and
audit routes on top of the same two predicates:

* ``require_chat_enabled`` — 503 ``chat_disabled`` when ``HERMES_CHAT_ENABLED``
  is false. Every Hermes-facing route depends on it.
* ``require_chat_configured`` — 503 ``chat_misconfigured`` when chat is on but
  its key or model is missing. The message names the variables, so an operator
  can tell "turned off" from "turned on but broken" (FR-028).

``GET /admin/chat/audit`` and ``DELETE /admin/chat/threads/{thread_id}`` stay
reachable with chat off, so they depend on neither predicate. They are listed in
``GATE_EXEMPT_PATHS`` so CHAT-003, CHAT-008, and CHAT-012 cannot drift.

The two codes and both sentences live here and nowhere else: the UI branches on
the code, and an edit to a sentence must not need a second edit in TypeScript.
"""

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.api.deps import get_settings
from deepfix_rag.api.errors import SERVICE_UNAVAILABLE
from deepfix_rag.config.settings import Settings

router = APIRouter(tags=["admin-chat"])

DISABLED_CODE = "chat_disabled"
DISABLED_MESSAGE = "Hermes chat is turned off on this server."
MISCONFIGURED_CODE = "chat_misconfigured"
MISCONFIGURED_MESSAGE = (
    "Admin chat is not configured on this server: set HERMES_CHAT_API_SERVER_KEY "
    "and HERMES_CHAT_MODEL or HERMES_MODEL."
)
# The two SQLite-only routes (D-4): an audit read and a thread delete serve what
# chat already wrote, so turning chat off must not hide or strand them.
GATE_EXEMPT_PATHS: tuple[str, ...] = (
    "/admin/chat/audit",
    "/admin/chat/threads/{thread_id}",
)


def chat_error(code: str, message: str) -> HTTPException:
    """The 503 body shape the chat routes use. FastAPI nests it under ``detail``."""
    return HTTPException(
        status_code=SERVICE_UNAVAILABLE,
        detail={"code": code, "message": message},
    )


def require_chat_enabled(settings: Settings) -> None:
    """Raise the disabled 503 when chat is off. Returns quietly when it is on."""
    if not settings.HERMES_CHAT_ENABLED:
        raise chat_error(DISABLED_CODE, DISABLED_MESSAGE)


def chat_model(settings: Settings) -> str:
    """``HERMES_CHAT_MODEL``, falling back to ``HERMES_MODEL`` (D-3)."""
    return (settings.HERMES_CHAT_MODEL or "").strip() or (settings.HERMES_MODEL or "").strip()


def require_chat_configured(settings: Settings) -> None:
    """Raise the misconfigured 503 when the key or the model is missing."""
    key = (settings.HERMES_CHAT_API_SERVER_KEY or "").strip()
    if not key or not chat_model(settings):
        raise chat_error(MISCONFIGURED_CODE, MISCONFIGURED_MESSAGE)


def require_chat(settings: Settings) -> None:
    """The enabled gate followed by the configuration check, in that order."""
    require_chat_enabled(settings)
    require_chat_configured(settings)


def chat_gate(request: Request) -> None:
    """FastAPI dependency for a Hermes-facing chat route: enable, then configure."""
    require_chat(get_settings(request))

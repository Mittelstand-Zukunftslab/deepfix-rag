"""Admin Hermes chat routes.

The browser calls one Hermes-facing route, ``POST /admin/chat/completions``,
through the web app's same-origin ``/api`` proxy. deepfix-api forwards to the
chat Hermes profile on the internal Compose network, injecting the chat bearer
server-side, and relays the reply to the browser as the **Vercel AI SDK UI
message stream** that the chat UI's transport decodes. Hermes itself stays
OpenAI-compatible: only the browser-facing encoding is translated
(``deepfix_rag.api.chat_stream``).

Two predicates gate every Hermes-facing route, in this order:

* ``require_chat_enabled`` — 503 ``chat_disabled`` when ``HERMES_CHAT_ENABLED``
  is false.
* ``require_chat_configured`` — 503 ``chat_misconfigured`` when chat is on but
  its key or model is missing.

Identity and role are checked first on every route, so an unsigned caller still
gets 401 and a member 403 while chat is off, and the disabled 503 can never be a
probe. ``GET /admin/chat/audit`` and ``DELETE /admin/chat/threads/{thread_id}``
depend on neither predicate: they serve what chat already wrote (D-4).
"""

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import httpx

from deepfix_rag import admin_chat, optimization
from deepfix_rag.api import chat_stream
from deepfix_rag.api.chat_turns import TurnRegistry, registry_for, resume_frames
from deepfix_rag.api.deps import get_settings, require_admin
from deepfix_rag.api.errors import (
    BAD_GATEWAY,
    BAD_REQUEST,
    CONFLICT,
    FORBIDDEN,
    INTERNAL_SERVER_ERROR,
    NOT_FOUND,
    SERVICE_UNAVAILABLE,
    TOO_MANY_REQUESTS,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.identity import User
from deepfix_rag.api.routes.optimization import cancel_run_for, start_run_for
from deepfix_rag.api.schemas import (
    ChatActionResponse,
    ChatAuditResponse,
    ChatCompletionRequest,
    ChatMessageResponse,
    ChatThreadDetailResponse,
    ChatThreadResponse,
    ChatThreadRunResponse,
    ChatThreadSummaryResponse,
    ChatTurnResponse,
    OptimizationRunResponse,
)
from deepfix_rag.config.settings import LOGGER, Settings

router = APIRouter(tags=["admin-chat"])

DISABLED_CODE = "chat_disabled"
DISABLED_MESSAGE = "Hermes chat is turned off on this server."
MISCONFIGURED_CODE = "chat_misconfigured"
MISCONFIGURED_MESSAGE = (
    "Admin chat is not configured on this server: set HERMES_CHAT_API_SERVER_KEY "
    "and HERMES_CHAT_MODEL or HERMES_MODEL."
)
STALE_HISTORY_MESSAGE = "The conversation is out of date. Reload the thread."
NOTHING_TO_REGENERATE_MESSAGE = "There is no earlier answer to regenerate."
TOO_LONG_MESSAGE = "This reply took too long and was stopped."
UPSTREAM_FAILED_MESSAGE = "The assistant could not answer. Try again."
CANCELLED_MESSAGE = "The browser closed the connection."
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


@router.post(
    "/admin/chat/completions",
    operation_id="admin_chat_completion",
    response_class=StreamingResponse,
    responses={
        **error_responses(UNAUTHORIZED, FORBIDDEN, BAD_REQUEST, BAD_GATEWAY, SERVICE_UNAVAILABLE),
        200: {
            "description": "The AI SDK UI message stream for one turn.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
    summary="Send one chat turn and stream the reply",
)
async def admin_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
) -> StreamingResponse:
    """Validate the turn, then relay the reply as an AI SDK UI message stream."""
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)

    thread = _owned_thread(settings, payload.thread_id, user.id)
    stored = admin_chat.thread_messages(settings.product_db_path, thread.thread_id, user.id)
    # A regenerate names the answer it replaces. The UI cannot send that id (the
    # AI SDK's reload drops the message it is replacing), so the server resolves
    # it from the thread: the newest answered turn. Everything downstream then
    # treats it exactly like a retry, which is what keeps the checks honest.
    regenerate_of = _regenerate_target(stored) if payload.regenerate else None
    retry_of = regenerate_of if regenerate_of is not None else payload.retry_of
    _check_limits(settings, payload)
    _check_history(stored, payload, retry_of)
    # A retry replaces the answer it names, so that turn's messages stay out of
    # the upstream conversation while the thread still shows both turns.
    history = _retried_history(stored, retry_of)
    prompt = payload.messages[-1].content

    refusal = admin_chat.check_rate_limit(
        settings.product_db_path,
        user.id,
        max_turns=settings.ADMIN_CHAT_MAX_TURNS_PER_WINDOW,
        window_seconds=settings.ADMIN_CHAT_TURN_WINDOW_SECONDS,
        max_concurrent=settings.ADMIN_CHAT_MAX_CONCURRENT_TURNS,
    )
    if refusal is not None:
        if refusal.audit and admin_chat.should_audit_rate_limit(settings.product_db_path, user.id):
            _audit(
                settings,
                user,
                None,
                thread.thread_id,
                "rate_limited",
                f"refused: {refusal.message}",
            )
            _log(thread.thread_id, "-", "rate_limited")
        raise HTTPException(
            status_code=TOO_MANY_REQUESTS,
            detail=refusal.message,
            headers={"Retry-After": str(refusal.retry_after)},
        )
    try:
        turn = admin_chat.start_turn(
            settings.product_db_path,
            thread.thread_id,
            user.id,
            admin_email=user.email,
            retry_of=retry_of,
            request_chars=sum(len(item.content) for item in payload.messages),
            reuse_turn_id=regenerate_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    if regenerate_of is None:
        admin_chat.append_message(
            settings.product_db_path,
            thread.thread_id,
            role="user",
            content=prompt,
            turn_id=turn.turn_id,
        )
        # The assistant message exists from the first moment, so a resume has a
        # row to replay even when the reply ends empty.
        reply = admin_chat.append_message(
            settings.product_db_path,
            thread.thread_id,
            role="assistant",
            content="",
            turn_id=turn.turn_id,
        )
    else:
        # A regenerate reuses the turn and its rows: the same user and assistant
        # messages, with the assistant text restarted, so the thread never shows
        # the question or the answer twice.
        _, reply = admin_chat.replace_turn_messages(
            settings.product_db_path,
            thread.thread_id,
            turn.turn_id,
            prompt=prompt,
        )
    registry = registry_for(request.app.state)
    registry.open(turn.turn_id, thread.thread_id)
    _audit(
        settings,
        user,
        turn.turn_id,
        thread.thread_id,
        "turn_started",
        f"turn regenerated from {regenerate_of}" if regenerate_of else "turn started",
    )
    _log(thread.thread_id, turn.turn_id, "turn_started")

    upstream = await _open_upstream(
        settings,
        history=history,
        prompt=prompt,
        thread_id=thread.thread_id,
        turn_id=turn.turn_id,
        digest=_digest(request, settings),
    )
    if upstream is None:
        # A refusal before the first byte is an honest HTTP status, not a stream
        # that opens and then reports failure.
        _settle(
            settings,
            user=user,
            thread_id=thread.thread_id,
            turn_id=turn.turn_id,
            status="failed",
            failure=UPSTREAM_FAILED_MESSAGE,
            first_token_ms=None,
        )
        raise HTTPException(status_code=BAD_GATEWAY, detail=UPSTREAM_FAILED_MESSAGE)

    return StreamingResponse(
        _relay(
            request,
            settings=settings,
            user=user,
            registry=registry,
            thread_id=thread.thread_id,
            turn=turn,
            message_id=reply.message_id,
            upstream=upstream,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            chat_stream.STREAM_HEADER: chat_stream.STREAM_HEADER_VALUE,
        },
    )


def _owned_thread(settings: Settings, thread_id: str, admin_id: str) -> admin_chat.Thread:
    try:
        return admin_chat.get_thread(settings.product_db_path, thread_id, admin_id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail="No chat thread with that id.") from exc


def _retried_history(
    history: list[admin_chat.Message], retry_of: str | None
) -> list[admin_chat.Message]:
    """The stored history minus the turn being retried, whose answer is replaced."""
    if retry_of is None:
        return list(history)
    return [message for message in history if message.turn_id != retry_of]


def _regenerate_target(history: list[admin_chat.Message]) -> str:
    """The turn a regenerate replaces: the newest answered turn in the thread.

    Regenerating always concerns the last assistant answer, which is what the
    UI's reload control acts on. A thread with no answer yet has nothing to
    replace, so it is a 400 rather than a silent no-op.
    """
    answered = [message.turn_id for message in history if message.role == "assistant"]
    target = next((turn_id for turn_id in reversed(answered) if turn_id), None)
    if target is None:
        raise HTTPException(status_code=BAD_REQUEST, detail=NOTHING_TO_REGENERATE_MESSAGE)
    return target


def _check_history(
    history: list[admin_chat.Message], payload: ChatCompletionRequest, retry_of: str | None
) -> None:
    """The client's array must agree with the stored thread and end with a user turn.

    A retry drops the answer it replaces, so that one assistant message is left
    out of the comparison. Everything else has to match exactly: the browser
    cannot rewrite history, and cannot claim a turn that never happened.
    """
    if payload.messages[-1].role != "user":
        raise HTTPException(status_code=BAD_REQUEST, detail=STALE_HISTORY_MESSAGE)
    expected = _retried_history(history, retry_of)
    if len(payload.messages) - 1 != len(expected):
        raise HTTPException(status_code=BAD_REQUEST, detail=STALE_HISTORY_MESSAGE)
    for stored, sent in zip(expected, payload.messages[:-1], strict=True):
        if stored.role != sent.role or stored.content != sent.content:
            raise HTTPException(status_code=BAD_REQUEST, detail=STALE_HISTORY_MESSAGE)


def _check_limits(settings: Settings, payload: ChatCompletionRequest) -> None:
    if len(payload.messages) > settings.ADMIN_CHAT_MAX_MESSAGES:
        raise HTTPException(
            status_code=BAD_REQUEST,
            detail=(
                f"A conversation of at most {settings.ADMIN_CHAT_MAX_MESSAGES} messages "
                "can be sent."
            ),
        )
    total = sum(len(item.content) for item in payload.messages)
    if total > settings.ADMIN_CHAT_MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=BAD_REQUEST,
            detail=(
                f"That message is too long: {total} characters, and the limit is "
                f"{settings.ADMIN_CHAT_MAX_PROMPT_CHARS}."
            ),
        )


def _audit(
    settings: Settings,
    user: User,
    turn_id: str | None,
    thread_id: str,
    kind: str,
    detail: str,
) -> None:
    admin_chat.record_audit(
        settings.product_db_path,
        admin_id=user.id,
        admin_email=user.email,
        kind=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        detail=detail,
        private_paths=_private_paths(settings),
    )


def _private_paths(settings: Settings) -> tuple[str, str]:
    return (str(settings.PRODUCT_DATA_DIR), str(settings.product_db_path))


def _log(thread_id: str, turn_id: str, event: str) -> None:
    """One structured line per turn start and terminal state (NFR-8)."""
    LOGGER.info("chat thread=%s turn=%s event=%s", thread_id, turn_id, event)


def upstream_messages(
    settings: Settings,
    history: list[admin_chat.Message],
    prompt: str,
    *,
    thread_id: str,
    turn_id: str,
    digest: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """The system message plus the stored thread plus the new user message.

    Never the client's earlier array: the browser cannot rewrite the policy or
    claim a history that never happened (FR-005). The system message is rebuilt
    on every turn, digest included, so it always describes the server as it is
    now rather than as it was when the thread started.
    """
    system = admin_chat.system_prompt(
        settings, thread_id=thread_id, turn_id=turn_id, digest=digest
    )
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": item.role, "content": item.content} for item in history)
    messages.append({"role": "user", "content": prompt})
    return messages


def _digest(request: Request, settings: Settings) -> dict[str, Any]:
    """The status digest, or a 500 naming the setting when it cannot fit (CHAT-004)."""
    scheduler = getattr(request.app.state, "optimization", None)
    if scheduler is None:
        return {}
    try:
        return admin_chat.status_digest(settings, scheduler)
    except ValueError as exc:
        raise HTTPException(status_code=INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


async def _open_upstream(
    settings: Settings,
    *,
    history: list[admin_chat.Message],
    prompt: str,
    thread_id: str,
    turn_id: str,
    digest: dict[str, Any] | None = None,
) -> "_Upstream | None":
    """Open Hermes's stream before any byte reaches the browser.

    A refusal here is a 502 with a sentence, which is what a caller can act on;
    once the response has started, the only honest failure is an ``error`` part.
    """
    body = {
        "model": chat_model(settings),
        "stream": True,
        "messages": upstream_messages(
            settings, history, prompt, thread_id=thread_id, turn_id=turn_id, digest=digest
        ),
    }
    headers = {
        "Authorization": f"Bearer {(settings.HERMES_CHAT_API_SERVER_KEY or '').strip()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    client = httpx.AsyncClient(timeout=httpx.Timeout(settings.ADMIN_CHAT_TURN_TIMEOUT_SECONDS))
    try:
        request = client.build_request(
            "POST",
            settings.HERMES_CHAT_BASE_URL.rstrip("/") + "/v1/chat/completions",
            headers=headers,
            json=body,
        )
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        LOGGER.warning("chat upstream request failed: %s", type(exc).__name__)
        return None
    if response.status_code >= 400:
        await response.aclose()
        await client.aclose()
        LOGGER.warning("chat upstream answered %s", response.status_code)
        return None
    return _Upstream(client=client, response=response)


class _Upstream:
    """One open Hermes response and the client that owns its socket."""

    def __init__(self, *, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self.client = client
        self.response = response

    async def close(self) -> None:
        await self.response.aclose()
        await self.client.aclose()


async def _relay(
    request: Request,
    *,
    settings: Settings,
    user: User,
    registry: "TurnRegistry",
    thread_id: str,
    turn: admin_chat.Turn,
    message_id: str,
    upstream: _Upstream,
) -> AsyncIterator[str]:
    """Stream one open upstream turn, settling its row and audit on every exit path."""
    turn_id = turn.turn_id
    text_id = f"{turn_id}-text"
    collected: list[str] = []
    started = time.monotonic()
    first_token_ms: int | None = None
    status = "failed"
    failure: str | None = None
    actions: list[admin_chat.Action] = []
    deadline = started + settings.ADMIN_CHAT_TURN_TIMEOUT_SECONDS
    yield chat_stream.sse(chat_stream.start_part(turn_id))
    yield chat_stream.sse(chat_stream.text_start(text_id))
    try:
        async for line in upstream.response.aiter_lines():
            if time.monotonic() > deadline:
                raise _TurnTimeout()
            if await request.is_disconnected():
                raise _ClientGone()
            for payload in _payloads(line):
                parts, text = chat_stream.read_chunk(payload, text_id)
                if text:
                    collected.append(text)
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    # Written on every delta, so a resume replays what the browser saw.
                    admin_chat.update_assistant_text(
                        settings.product_db_path, message_id, "".join(collected)
                    )
                for part in parts:
                    frame = chat_stream.sse(part)
                    registry.publish(turn_id, frame)
                    yield frame
        status = "completed"
    except _TurnTimeout:
        failure = TOO_LONG_MESSAGE
        yield chat_stream.sse(chat_stream.error_part(TOO_LONG_MESSAGE))
    except _ClientGone:
        status = "cancelled"
        failure = CANCELLED_MESSAGE
    except (asyncio.CancelledError, GeneratorExit):
        # ASGI closes this generator when the browser goes away: a cancelled
        # task or a ``GeneratorExit``. That is a normal end (NFR-1), not an
        # exception to log, and the partial text stays stored.
        status = "cancelled"
        failure = CANCELLED_MESSAGE
        raise
    except httpx.HTTPError as exc:
        failure = UPSTREAM_FAILED_MESSAGE
        LOGGER.warning("chat upstream stream failed: %s", type(exc).__name__)
        yield chat_stream.sse(chat_stream.error_part(failure))
    finally:
        await upstream.close()
        registry.close(turn_id)
        _settle(
            settings,
            user=user,
            thread_id=thread_id,
            turn_id=turn_id,
            status=status,
            failure=failure,
            first_token_ms=first_token_ms,
        )
    if status == "cancelled":
        return
    if status == "completed":
        # Only a completed reply proposes anything: a cancelled or failed turn
        # must never hand the admin a live action.
        actions = _store_actions(settings, user, thread_id=thread_id, turn_id=turn_id)
    yield chat_stream.sse(chat_stream.text_end(text_id))
    yield chat_stream.sse(
        chat_stream.turn_part(turn_id, status, [_action_card(a) for a in actions])
    )
    yield chat_stream.sse(chat_stream.finish_part("stop" if status == "completed" else "error"))
    yield chat_stream.DONE_FRAME


def _store_actions(
    settings: Settings, user: User, *, thread_id: str, turn_id: str
) -> list[admin_chat.Action]:
    """Scan the stored reply, store up to two cards, and audit what happened."""
    text = admin_chat.stored_assistant_text(settings.product_db_path, turn_id)
    parsed = admin_chat.parse_actions(text, db_path=settings.product_db_path)
    stored = admin_chat.store_actions(
        settings.product_db_path,
        thread_id=thread_id,
        turn_id=turn_id,
        parsed=parsed,
        ttl_seconds=settings.ADMIN_CHAT_ACTION_TTL_SECONDS,
    )
    for action in stored:
        _audit(
            settings,
            user,
            turn_id,
            thread_id,
            "action_proposed",
            f"kind={action.kind} action={action.action_id}",
        )
    if parsed.dropped:
        # A dropped block is counted, never surfaced as an error the admin must
        # handle: the reply still reads normally, the audit row says why.
        _audit(settings, user, turn_id, thread_id, "action_proposed", parsed.summary())
    return stored


def _action_card(action: admin_chat.Action) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "turn_id": action.turn_id,
        "kind": action.kind,
        "payload": json.loads(action.payload) if action.payload else {},
        "status": action.status,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
    }


class _ClientGone(Exception):
    """The browser closed the connection."""


class _UpstreamError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _TurnTimeout(Exception):
    """The wall-clock deadline passed."""


def _payloads(line: str) -> list[dict[str, Any]]:
    """Every JSON object on one SSE data line, or nothing for a comment."""
    if not line.startswith("data:"):
        return []
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning("chat upstream sent a non-JSON frame; dropped")
        return []
    if not isinstance(payload, dict):
        LOGGER.warning("chat upstream sent a non-object frame; dropped")
        return []
    return [payload]


def _settle(
    settings: Settings,
    *,
    user: User,
    thread_id: str,
    turn_id: str,
    status: str,
    failure: str | None,
    first_token_ms: int | None,
) -> None:
    """Settle the turn and write the matching audit row."""
    admin_chat.finish_turn(
        settings.product_db_path,
        turn_id,
        status=status,
        error=failure,
        first_token_ms=first_token_ms,
        private_paths=_private_paths(settings),
    )
    kind = _SETTLED_KIND.get(status, "turn_failed")
    detail = f"turn {status}" if not failure else f"turn {status}: {failure}"
    admin_chat.record_audit(
        settings.product_db_path,
        admin_id=user.id,
        admin_email=user.email,
        kind=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        detail=detail,
        private_paths=_private_paths(settings),
    )
    _log(thread_id, turn_id, kind)


_SETTLED_KIND = {
    "completed": "turn_completed",
    "cancelled": "turn_cancelled",
    "failed": "turn_failed",
}


def _thread_response(thread: admin_chat.Thread) -> ChatThreadResponse:
    return ChatThreadResponse(
        thread_id=thread.thread_id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _message_response(message: admin_chat.Message) -> ChatMessageResponse:
    return ChatMessageResponse(
        message_id=message.message_id,
        turn_id=message.turn_id,
        role=message.role,
        seq=message.seq,
        content=message.content,
        created_at=message.created_at,
    )


def _turn_response(turn: admin_chat.Turn) -> ChatTurnResponse:
    return ChatTurnResponse(
        turn_id=turn.turn_id,
        status=turn.status,
        retry_of=turn.retry_of,
        error=turn.error,
        first_token_ms=turn.first_token_ms,
        started_at=turn.started_at,
        ended_at=turn.ended_at,
    )


def _action_response(action: admin_chat.Action) -> ChatActionResponse:
    return ChatActionResponse(
        action_id=action.action_id,
        turn_id=action.turn_id,
        kind=action.kind,
        payload=action.payload,
        status=action.status,
        created_at=action.created_at,
        expires_at=action.expires_at,
        decided_at=action.decided_at,
        result=action.result,
    )


@router.get(
    "/admin/chat/threads",
    operation_id="list_admin_chat_threads",
    response_model=list[ChatThreadSummaryResponse],
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, SERVICE_UNAVAILABLE),
    summary="List this admin's chat threads",
)
def list_admin_chat_threads(request: Request, limit: int = 50) -> list[ChatThreadSummaryResponse]:
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    return [
        ChatThreadSummaryResponse(
            **_thread_response(thread).model_dump(),
            last_turn_status=admin_chat.last_turn_status(
                settings.product_db_path, thread.thread_id
            ),
        )
        for thread in admin_chat.list_threads(settings.product_db_path, user.id, limit=limit)
    ]


@router.post(
    "/admin/chat/threads",
    operation_id="create_admin_chat_thread",
    response_model=ChatThreadResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, SERVICE_UNAVAILABLE),
    summary="Open a new chat thread",
)
def create_admin_chat_thread(request: Request) -> ChatThreadResponse:
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    thread = admin_chat.create_thread(settings.product_db_path, user.id, admin_email=user.email)
    return _thread_response(thread)


@router.get(
    "/admin/chat/threads/{thread_id}",
    operation_id="get_admin_chat_thread",
    response_model=ChatThreadDetailResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, SERVICE_UNAVAILABLE),
    summary="Show one chat thread with its messages, turns, and actions",
)
def get_admin_chat_thread(thread_id: str, request: Request) -> ChatThreadDetailResponse:
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    thread = _owned_thread(settings, thread_id, user.id)
    return ChatThreadDetailResponse(
        thread=_thread_response(thread),
        messages=[
            _message_response(message)
            for message in admin_chat.thread_messages(
                settings.product_db_path, thread.thread_id, user.id
            )
        ],
        turns=[
            _turn_response(turn)
            for turn in admin_chat.thread_turns(
                settings.product_db_path, thread.thread_id, user.id
            )
        ],
        actions=[
            _action_response(action)
            for action in admin_chat.thread_actions(
                settings.product_db_path, thread.thread_id, user.id
            )
        ],
    )


@router.delete(
    "/admin/chat/threads/{thread_id}",
    operation_id="delete_admin_chat_thread",
    status_code=204,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN),
    summary="Delete one chat thread for good",
)
def delete_admin_chat_thread(thread_id: str, request: Request) -> Response:
    """Hard delete, children before parent. Audit rows survive (D-4).

    Answers 204 with chat on or off, with the key set or not: it is one of the
    two SQLite-only routes this feature keeps reachable.
    """
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    # An id that is not this admin's thread answers 204 without creating one, so
    # a delete is idempotent and never a way to probe another admin's ids (D-4).
    admin_chat.delete_thread(settings.product_db_path, thread_id, user.id)
    return Response(status_code=204)


@router.get(
    "/admin/chat/turns/{turn_id}/stream",
    operation_id="resume_admin_chat_turn",
    response_class=StreamingResponse,
    responses={
        **error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, SERVICE_UNAVAILABLE),
        200: {
            "description": "The AI SDK UI message stream, replayed and then tailed.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
    summary="Resume a reply that is in progress or already finished",
)
def resume_admin_chat_turn(turn_id: str, request: Request) -> StreamingResponse:
    """Replay the stored text, then tail the turn if it is running in this process."""
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    try:
        turn = admin_chat.turn_status(settings.product_db_path, turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail="No chat turn with that id.") from exc
    _owned_thread(settings, turn.thread_id, user.id)
    return StreamingResponse(
        resume_frames(
            registry=registry_for(request.app.state),
            db_path=settings.product_db_path,
            turn_id=turn_id,
            text_id=f"{turn_id}-text",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            chat_stream.STREAM_HEADER: chat_stream.STREAM_HEADER_VALUE,
        },
    )


@router.get(
    "/admin/chat/audit",
    operation_id="list_admin_chat_audit",
    response_model=list[ChatAuditResponse],
    responses=error_responses(UNAUTHORIZED, FORBIDDEN),
    summary="List the chat audit trail",
)
def list_admin_chat_audit(request: Request, limit: int = 50) -> list[ChatAuditResponse]:
    """The newest audit rows, admin only, with `thread_deleted` computed at read time.

    One of the two SQLite-only routes: it answers with chat off and with the
    chat key unset, because turning chat off must not hide what chat did (D-4).
    """
    require_admin(request, "Only admins can view the chat audit.")
    settings = get_settings(request)
    return [
        ChatAuditResponse(
            event_id=entry.event_id,
            at=entry.at,
            admin_id=entry.admin_id,
            admin_email=entry.admin_email,
            thread_id=entry.thread_id,
            turn_id=entry.turn_id,
            kind=entry.kind,
            detail=entry.detail,
            thread_deleted=entry.thread_deleted,
        )
        for entry in admin_chat.list_audit(settings.product_db_path, limit=limit)
    ]


@router.post(
    "/admin/chat/actions/{action_id}/confirm",
    operation_id="confirm_admin_chat_action",
    response_model=OptimizationRunResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, CONFLICT, SERVICE_UNAVAILABLE),
    summary="Confirm a proposed action and run it",
)
def confirm_admin_chat_action(action_id: str, request: Request) -> OptimizationRunResponse:
    """Run the action the assistant proposed, because the admin pressed Confirm.

    This is the only chat path that can change run state, and it reaches the
    same ``start_run_for``/``cancel_run_for`` helpers the Optimization screen
    uses. A card is confirmable while its turn still streams: the conversation
    plane and the job plane are separate, and ``RunInProgress`` is the real
    guard against two runs.
    """
    user = require_admin(request, "Only admins can confirm a chat action.")
    settings = get_settings(request)
    chat_gate(request)
    action = _owned_action(settings, action_id, user.id)
    action = admin_chat.expire_if_due(settings.product_db_path, action.action_id)
    if action.status == "expired":
        _audit(
            settings, user, action.turn_id, action.thread_id, "action_expired", action.action_id
        )
        raise HTTPException(
            status_code=CONFLICT, detail="This action has expired. Ask the assistant again."
        )
    if action.status != "proposed":
        raise HTTPException(status_code=CONFLICT, detail="This action has already been decided.")
    payload = json.loads(action.payload) if action.payload else {}
    try:
        if action.kind == "start_optimization_run":
            response = start_run_for(request)
        elif action.kind == "cancel_optimization_run":
            response = cancel_run_for(request, str(payload.get("run_id") or ""))
        else:
            raise HTTPException(
                status_code=BAD_REQUEST, detail="This action kind is not supported."
            )
    except HTTPException:
        # A retryable failure (conflict, unavailable) leaves the card proposed.
        raise
    admin_chat.decide_action(
        settings.product_db_path,
        action.action_id,
        status="confirmed",
        admin_id=user.id,
        result=json.dumps({"run_id": response.run_id, "status": response.status}, sort_keys=True),
    )
    _audit(
        settings,
        user,
        action.turn_id,
        action.thread_id,
        "action_confirmed",
        f"kind={action.kind} run={response.run_id} status={response.status}",
    )
    _log(action.thread_id, action.turn_id, "action_confirmed")
    if response.run_id:
        admin_chat.link_run(settings.product_db_path, action.thread_id, response.run_id)
    return response


@router.post(
    "/admin/chat/actions/{action_id}/reject",
    operation_id="reject_admin_chat_action",
    response_model=ChatActionResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, CONFLICT),
    summary="Dismiss a proposed action",
)
def reject_admin_chat_action(action_id: str, request: Request) -> ChatActionResponse:
    """Mark a proposed card dismissed. A decided card is 409."""
    user = require_admin(request, "Only admins can dismiss a chat action.")
    settings = get_settings(request)
    chat_gate(request)
    action = _owned_action(settings, action_id, user.id)
    action = admin_chat.expire_if_due(settings.product_db_path, action.action_id)
    if action.status != "proposed":
        raise HTTPException(status_code=CONFLICT, detail="This action has already been decided.")
    admin_chat.decide_action(
        settings.product_db_path, action.action_id, status="rejected", admin_id=user.id
    )
    _audit(settings, user, action.turn_id, action.thread_id, "action_rejected", action.kind)
    return _action_response(admin_chat.get_action(settings.product_db_path, action.action_id))


def _owned_action(settings: Settings, action_id: str, admin_id: str) -> admin_chat.Action:
    """The caller's own card. Another admin's id is 404, never 403."""
    try:
        action = admin_chat.get_action(settings.product_db_path, action_id)
        admin_chat.get_thread(settings.product_db_path, action.thread_id, admin_id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail="No chat action with that id.") from exc
    return action


@router.post(
    "/admin/chat/threads/{thread_id}/runs/{run_id}",
    operation_id="link_admin_chat_thread_run",
    response_model=ChatThreadRunResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, SERVICE_UNAVAILABLE),
    summary="Link an optimization run to a thread",
)
def link_admin_chat_thread_run(
    thread_id: str, run_id: str, request: Request
) -> ChatThreadRunResponse:
    """Link a run so the thread can show its progress. Linking twice is idempotent."""
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    _owned_thread(settings, thread_id, user.id)
    try:
        optimization.get_run(settings.product_db_path, run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=NOT_FOUND, detail="No optimization run with that id."
        ) from exc
    admin_chat.link_run(settings.product_db_path, thread_id, run_id)
    items = _run_items(settings, thread_id, user.id)
    return next(item for item in items if item.run_id == run_id)


@router.get(
    "/admin/chat/threads/{thread_id}/runs",
    operation_id="list_admin_chat_thread_runs",
    response_model=list[ChatThreadRunResponse],
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, SERVICE_UNAVAILABLE),
    summary="Show the runs linked to a thread",
)
def list_admin_chat_thread_runs(thread_id: str, request: Request) -> list[ChatThreadRunResponse]:
    """Each linked run's status, deltas, MLflow link, and redacted step tail."""
    user = require_admin(request, "Only admins can use Hermes chat.")
    settings = get_settings(request)
    chat_gate(request)
    # A dead job must not be reported as running forever: the same arguments the
    # Optimization screen uses, so the two cannot disagree about staleness.
    _owned_thread(settings, thread_id, user.id)
    optimization.fail_stale_runs(
        settings.product_db_path,
        timeout_seconds=settings.OPTIMIZATION_RUN_TIMEOUT_HOURS * 60 * 60,
        options=optimization.settings_from(settings),
    )
    return _run_items(settings, thread_id, user.id)


def _run_items(settings: Settings, thread_id: str, admin_id: str) -> list[ChatThreadRunResponse]:
    private = _private_paths(settings)
    items = admin_chat.linked_runs(
        settings.product_db_path,
        thread_id,
        admin_id,
        step_tail=settings.ADMIN_CHAT_STEP_TAIL,
        step_read_bytes=settings.ADMIN_CHAT_STEP_READ_BYTES,
        private_paths=private,
        # The chat key is a secret by name, but a test (and a deployment with a
        # different settings source) has it in Settings rather than the process
        # environment, so it is passed explicitly as well as being in the list.
        secrets=((settings.HERMES_CHAT_API_SERVER_KEY or "").strip(),),
        step_log_dir=Path(settings.PRODUCT_DATA_DIR) / "optimization-logs",
        url_for=optimization.mlflow_run_url,
    )
    return [
        ChatThreadRunResponse(
            run_id=item.run_id,
            status=item.status,
            trigger=item.trigger,
            reason=item.reason,
            started_at=item.started_at,
            ended_at=item.ended_at,
            primary_metric=item.primary_metric,
            dev_delta=item.dev_delta,
            holdout_delta=item.holdout_delta,
            holdout_wins=item.holdout_wins,
            holdout_losses=item.holdout_losses,
            mlflow_run_id=item.mlflow_run_id,
            mlflow_url=item.mlflow_url,
            steps=item.steps,
        )
        for item in items
    ]

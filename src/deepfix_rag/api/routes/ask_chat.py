"""The member's Ask conversations: list, open, rename, and delete a thread.

A thread is a server object (D-5), so a reload or a second browser shows the
same history. Every handler resolves the caller first and then asks the store
for a thread that belongs to them: an unsigned caller is 401, and another
member's id is 404 rather than 403, because a 403 would confirm that the id
exists (NFR-4).

``DELETE`` answers 204 for the caller's own thread and 404 for anybody else's,
including one that has already been deleted. That idempotence is not free — the
second delete is a 404 — but D-9's consequence is what matters here: the thread
and its turns go, and the ``interactions`` rows and votes behind them stay, so a
member who tidies their history does not delete the optimizer's evidence.
"""

from fastapi import APIRouter, HTTPException, Request, Response

from deepfix_rag import ask_chat
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import BAD_REQUEST, NOT_FOUND, UNAUTHORIZED, error_responses
from deepfix_rag.api.identity import User
from deepfix_rag.api.schemas import (
    AskChatCitationResponse,
    AskChatRenameRequest,
    AskChatThreadDetailResponse,
    AskChatThreadResponse,
    AskChatTurnResponse,
)
from deepfix_rag.config.settings import Settings

router = APIRouter(tags=["ask-chat"])

NO_THREAD_MESSAGE = "No conversation with that id."


@router.get(
    "/ask/chat/threads",
    operation_id="list_ask_chat_threads",
    response_model=list[AskChatThreadResponse],
    responses=error_responses(UNAUTHORIZED),
    summary="List this member's conversations",
)
def list_ask_chat_threads(request: Request) -> list[AskChatThreadResponse]:
    """The member's threads, newest first, capped at 50 with no pagination (D-11).

    The cap is display-only: a thread past it is not hidden, expired, or deleted,
    and ``GET /ask/chat/threads/{thread_id}`` still loads it.
    """
    user = current_user(request)
    return [
        _thread_response(thread)
        for thread in ask_chat.list_threads(get_settings(request).product_db_path, user.id)
    ]


@router.post(
    "/ask/chat/threads",
    operation_id="create_ask_chat_thread",
    status_code=201,
    response_model=AskChatThreadResponse,
    responses=error_responses(UNAUTHORIZED),
    summary="Start a new conversation",
)
def create_ask_chat_thread(request: Request) -> AskChatThreadResponse:
    """Open one empty thread. Its title is derived from the first question later."""
    user = current_user(request)
    settings = get_settings(request)
    thread = ask_chat.create_thread(settings.product_db_path, user.id, owner_email=user.email)
    return _thread_response(thread)


@router.get(
    "/ask/chat/threads/{thread_id}",
    operation_id="get_ask_chat_thread",
    response_model=AskChatThreadDetailResponse,
    responses=error_responses(UNAUTHORIZED, NOT_FOUND),
    summary="Show one conversation with its turns",
)
def get_ask_chat_thread(thread_id: str, request: Request) -> AskChatThreadDetailResponse:
    """The thread and its turns in order, each with its grounding and citations."""
    user = current_user(request)
    settings = get_settings(request)
    thread = _owned_thread(settings, thread_id, user)
    return AskChatThreadDetailResponse(
        thread=_thread_response(thread),
        turns=[
            _turn_response(turn)
            for turn in ask_chat.thread_turns(settings.product_db_path, thread_id, user.id)
        ],
    )


@router.patch(
    "/ask/chat/threads/{thread_id}",
    operation_id="rename_ask_chat_thread",
    response_model=AskChatThreadResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED, NOT_FOUND),
    summary="Rename a conversation",
)
def rename_ask_chat_thread(
    thread_id: str, payload: AskChatRenameRequest, request: Request
) -> AskChatThreadResponse:
    """Set a title of 1 to 120 characters. A title outside that is 400, stored unchanged."""
    user = current_user(request)
    settings = get_settings(request)
    _owned_thread(settings, thread_id, user)
    try:
        thread = ask_chat.rename_thread(
            settings.product_db_path, thread_id, user.id, payload.title
        )
    except ValueError as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    return _thread_response(thread)


@router.delete(
    "/ask/chat/threads/{thread_id}",
    operation_id="delete_ask_chat_thread",
    status_code=204,
    responses=error_responses(UNAUTHORIZED, NOT_FOUND),
    summary="Delete a conversation, keeping the votes it recorded",
)
def delete_ask_chat_thread(thread_id: str, request: Request) -> Response:
    """Remove the thread and its turns. The interactions and votes behind them stay (D-9)."""
    user = current_user(request)
    settings = get_settings(request)
    if not ask_chat.delete_thread(settings.product_db_path, thread_id, user.id):
        raise HTTPException(status_code=NOT_FOUND, detail=NO_THREAD_MESSAGE)
    return Response(status_code=204)


def _owned_thread(settings: Settings, thread_id: str, user: User) -> ask_chat.Thread:
    """The caller's own thread. Another member's id is 404, never 403."""
    try:
        return ask_chat.get_thread(settings.product_db_path, thread_id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=NO_THREAD_MESSAGE) from exc


def _thread_response(thread: ask_chat.Thread) -> AskChatThreadResponse:
    return AskChatThreadResponse(
        thread_id=thread.thread_id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _turn_response(turn: ask_chat.Turn) -> AskChatTurnResponse:
    return AskChatTurnResponse(
        turn_id=turn.turn_id,
        seq=turn.seq,
        status=turn.status,
        retry_of=turn.retry_of,
        error=turn.error,
        grounding=turn.grounding,
        citations=[
            AskChatCitationResponse(
                marker=citation.marker, rank=citation.rank, doc_id=citation.doc_id
            )
            for citation in turn.citations
        ],
        retrieval_query=turn.retrieval_query,
        first_token_ms=turn.first_token_ms,
        started_at=turn.started_at,
        ended_at=turn.ended_at,
        interaction_id=turn.interaction_id,
        question=turn.question,
        answer=turn.answer,
    )

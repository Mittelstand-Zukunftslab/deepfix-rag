"""The member's Ask conversations: threads, turns, and the streamed turn.

A thread is a server object (D-5), so a reload or a second browser shows the
same history. Every handler resolves the caller first and then asks the store
for a thread that belongs to them: an unsigned caller is 401, and another
member's id is 404 rather than 403, because a 403 would confirm that the id
exists (NFR-4).

``POST /ask/chat/completions`` is the one route that produces an answer. It
speaks the AI SDK's UI message stream — the protocol the admin Hermes chat
already emits — so the browser's Assistant UI runtime renders text and tool
calls with no second protocol. The loop is ``ask_chat_turn.run_turn``; this
module only validates, opens and settles the row, and forwards parts.

``DELETE`` answers 204 for the caller's own thread and 404 for anybody else's,
including one that has already been deleted. That idempotence is not free — the
second delete is a 404 — but D-9's consequence is what matters here: the thread
and its turns go, and the ``interactions`` rows and votes behind them stay, so a
member who tidies their history does not delete the optimizer's evidence.
"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from deepfix_rag import ask_chat, ask_chat_turn
from deepfix_rag.api import chat_stream
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_GATEWAY,
    BAD_REQUEST,
    NOT_FOUND,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.identity import User
from deepfix_rag.api.schemas import (
    AskChatCitationResponse,
    AskChatCompletionRequest,
    AskChatRenameRequest,
    AskChatThreadDetailResponse,
    AskChatThreadResponse,
    AskChatTurnResponse,
)
from deepfix_rag.config.settings import LOGGER, Settings
from deepfix_rag.grounding import Grounding
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.service import INDEX_PREPARING
from deepfix_rag.tracing import trace_request

router = APIRouter(tags=["ask-chat"])

NO_THREAD_MESSAGE = "No conversation with that id."
STALE_THREAD_MESSAGE = "The conversation is out of date. Reload the thread."
NOTHING_TO_REGENERATE_MESSAGE = "There is no answer to regenerate yet."


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


@router.post(
    "/ask/chat/completions",
    operation_id="ask_chat_completion",
    response_class=StreamingResponse,
    responses={
        **error_responses(BAD_REQUEST, UNAUTHORIZED, NOT_FOUND, SERVICE_UNAVAILABLE, BAD_GATEWAY),
        200: {
            "description": "The AI SDK UI message stream for one turn.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
    summary="Ask one question and stream the answer",
)
async def ask_chat_completion(
    payload: AskChatCompletionRequest, request: Request
) -> StreamingResponse:
    """Open a turn, then stream the model's answer as AI SDK UI message parts.

    A failure discovered before the first byte is a JSON status with a sentence:
    another member's thread is 404, a last message that is not the member's is
    400, and no serving index is 503 with the sentence the Search page shows.
    Once the stream has started, the only honest failure is an ``error`` part.
    """
    user = current_user(request)
    settings = get_settings(request)
    thread = _owned_thread(settings, payload.thread_id, user)
    question = _question(payload)
    history = ask_chat.thread_turns(settings.product_db_path, thread.thread_id, user.id)
    regenerate_of = _regenerate_target(history) if payload.regenerate else None

    coordinator: IndexCoordinator = request.app.state.index
    if not _index_ready(coordinator):
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=INDEX_PREPARING)

    turn = ask_chat.open_turn(
        settings.product_db_path,
        thread.thread_id,
        user.id,
        question=question,
        retry_of=regenerate_of,
    )
    return StreamingResponse(
        _relay(
            request,
            settings=settings,
            user=user,
            thread=thread,
            turn=turn,
            question=question,
            history=[item for item in history if item.turn_id != regenerate_of],
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            chat_stream.STREAM_HEADER: chat_stream.STREAM_HEADER_VALUE,
        },
    )


async def _relay(
    request: Request,
    *,
    settings: Settings,
    user: User,
    thread: ask_chat.Thread,
    turn: ask_chat.Turn,
    question: str,
    history: list[ask_chat.Turn],
) -> AsyncIterator[str]:
    """Forward the turn's parts, then settle its row on every exit path.

    The terminal ``data-deepfix-turn`` is emitted before the stream closes, so a
    resume and the browser agree about the status. The stored row is written
    before that part goes out, because the part is the browser's cue to re-read.
    """
    turn_id = turn.turn_id
    outcome: ask_chat_turn.TurnOutcome | None = None
    status = "failed"
    failure: str | None = None
    yield chat_stream.sse(chat_stream.start_part(turn_id))
    try:
        async for produced in ask_chat_turn.run_turn(
            coordinator=request.app.state.index,
            settings=settings,
            history=history,
            question=question,
            is_disconnected=request.is_disconnected,
        ):
            if isinstance(produced, ask_chat_turn.TurnOutcome):
                outcome = produced
                continue
            yield chat_stream.sse(produced)
        status = "completed"
    except ask_chat_turn.TurnCancelled as exc:
        status = "cancelled"
        failure = ask_chat_turn.CANCELLED if "browser" in str(exc) else ask_chat_turn.TOO_LONG
    except ask_chat_turn.TurnFailed as exc:
        failure = str(exc)
        yield chat_stream.sse(chat_stream.error_part(failure))
    except ask_chat.IndexNotReadyError as exc:
        failure = str(exc)
        yield chat_stream.sse(chat_stream.error_part(failure))
    except Exception:
        LOGGER.exception("ask chat turn %s failed", turn_id)
        failure = ask_chat_turn.UPSTREAM_FAILED
        yield chat_stream.sse(chat_stream.error_part(failure))

    stored = _settle(
        settings,
        user=user,
        thread=thread,
        turn=turn,
        question=question,
        status=status,
        failure=failure,
        outcome=outcome,
    )
    grounding = stored.grounding or Grounding.NO_RELEVANT_SOURCES.value
    citations = [
        {"marker": c.marker, "rank": c.rank, "doc_id": c.doc_id} for c in stored.citations
    ]
    if outcome is not None:
        yield chat_stream.sse(
            chat_stream.grounding_part(
                grounding,
                citations,
                max_score=outcome.max_score,
                cutoff=outcome.cutoff,
            )
        )
    yield chat_stream.sse(
        chat_stream.turn_part(
            turn_id,
            status,
            grounding=grounding if outcome is not None else None,
            citations=citations,
        )
    )
    yield chat_stream.sse(chat_stream.finish_part("stop" if status == "completed" else "error"))
    yield chat_stream.DONE_FRAME


def _settle(
    settings: Settings,
    *,
    user: User,
    thread: ask_chat.Thread,
    turn: ask_chat.Turn,
    question: str,
    status: str,
    failure: str | None,
    outcome: ask_chat_turn.TurnOutcome | None,
) -> ask_chat.Turn:
    """Store the answer, its hits, and the turn's terminal state, in that order.

    The ``interactions`` row goes through ``interactions.record_interaction`` and
    is then linked to the thread and the turn, so votes, history, the MLflow
    assessment mirror, and the optimizer's export keep reading one shape (FR-019).
    """
    if outcome is not None:
        trace_id = trace_request(
            "ask_chat",
            turn.turn_id,
            {"question": question, "thread_id": thread.thread_id},
            {
                "answer": outcome.answer,
                "grounding": outcome.grounding,
                "passages": [
                    {"doc_id": passage.doc_id, "score": passage.score}
                    for passage in outcome.passages
                ],
            },
        )
        stored = ask_chat.record_turn_answer(
            settings.product_db_path,
            turn,
            question=question,
            answer=outcome.answer,
            results=outcome.retrieval_results,
            retrieval_query=outcome.retrieval_query,
            grounding=outcome.grounding,
            citations=outcome.citations,
            build_id=outcome.build_id,
            config_path=_config_path(settings),
            user_email=user.email,
            trace_id=trace_id,
        )
        ask_chat.link_interaction(settings.product_db_path, turn.turn_id, user.id, stored)
    return ask_chat.finish_turn(
        settings.product_db_path,
        turn.turn_id,
        user.id,
        status=status,
        error=failure,
        grounding=None if outcome is None else outcome.grounding,
        citations=None if outcome is None else _citations(outcome),
        retrieval_query=None if outcome is None else outcome.retrieval_query,
        first_token_ms=None if outcome is None else outcome.first_token_ms,
    )


def _citations(outcome: ask_chat_turn.TurnOutcome) -> list[ask_chat.Citation]:
    return [
        ask_chat.Citation(
            marker=int(item["marker"]), rank=int(item["rank"]), doc_id=str(item["doc_id"])
        )
        for item in outcome.citations
    ]


def _config_path(settings: Settings) -> str:
    """The champion file a turn was served from, for the stored interaction."""
    return f"{settings.WORKSPACE_DIR}/configs/champion.yaml"


def _index_ready(coordinator: IndexCoordinator) -> bool:
    lease = coordinator.checkout()
    if lease is None:
        return False
    coordinator.release(lease)
    return True


def _question(payload: AskChatCompletionRequest) -> str:
    """The newest message, which must be the member's own question (FR-011)."""
    last = payload.messages[-1]
    if last.role != "user":
        raise HTTPException(status_code=BAD_REQUEST, detail=ask_chat_turn.NON_USER_MESSAGE)
    question = last.content.strip()
    if not question:
        raise HTTPException(status_code=BAD_REQUEST, detail="Enter a question.")
    return question


def _regenerate_target(history: list[ask_chat.Turn]) -> str | None:
    """The newest answered turn, which a regenerate replaces in place (FR-015).

    The AI SDK's reload drops the message it is replacing, so the browser cannot
    name the turn; the server resolves it from the store exactly as the admin
    chat's ``_regenerate_target`` does.
    """
    answered = [turn for turn in history if turn.status in {"completed", "cancelled"}]
    if not answered:
        return None
    return answered[-1].turn_id


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

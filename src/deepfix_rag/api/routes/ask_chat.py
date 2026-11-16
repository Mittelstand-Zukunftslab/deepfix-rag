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

import asyncio
from collections.abc import AsyncIterator, Callable
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from deepfix_rag import ask_chat, ask_chat_turn
from deepfix_rag.api import chat_stream
from deepfix_rag.api.ask_chat_turns import registry_for, resume_frames
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_GATEWAY,
    BAD_REQUEST,
    CONFLICT,
    NOT_FOUND,
    SERVICE_UNAVAILABLE,
    TOO_MANY_REQUESTS,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.identity import User
from deepfix_rag.api.schemas import (
    AskChatCitationResponse,
    AskChatCompletionRequest,
    AskChatImportRequest,
    AskChatImportResponse,
    AskChatPassageResponse,
    AskChatRenameRequest,
    AskChatThreadDetailResponse,
    AskChatThreadResponse,
    AskChatTurnResponse,
)
from deepfix_rag.config.settings import LOGGER, Settings
from deepfix_rag.documents import document_filenames
from deepfix_rag.grounding import Grounding
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.interactions import (
    get_interaction,
    replace_interaction,
    set_trace_id,
    update_answer,
)
from deepfix_rag.service import INDEX_PREPARING
from deepfix_rag.tracing import TurnTrace, turn_trace

router = APIRouter(tags=["ask-chat"])

NO_THREAD_MESSAGE = "No conversation with that id."
NO_TURN_MESSAGE = "No answer with that id."
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


@router.post(
    "/ask/chat/threads/import",
    operation_id="import_ask_chat_sessions",
    response_model=AskChatImportResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED),
    summary="Import the browser's old conversations once",
)
def import_ask_chat_sessions(
    payload: AskChatImportRequest, request: Request
) -> AskChatImportResponse:
    """Bring a member's legacy localStorage sessions across, best effort (FR-033).

    The caller's own interactions only: a turn whose interaction is missing or
    belongs to somebody else is skipped and counted. The import never fabricates an
    interaction, so it cannot create a question the optimizer would score with no
    passages behind it.
    """
    user = current_user(request)
    settings = get_settings(request)
    result = ask_chat.import_sessions(
        settings.product_db_path,
        user.id,
        [session.model_dump(by_alias=False) for session in payload.sessions],
        owner_email=user.email,
    )
    return AskChatImportResponse(
        threads_created=result.threads_created,
        turns_created=result.turns_created,
        turns_skipped=result.turns_skipped,
    )


@router.get(
    "/ask/chat/threads/{thread_id}",
    operation_id="get_ask_chat_thread",
    response_model=AskChatThreadDetailResponse,
    responses=error_responses(UNAUTHORIZED, NOT_FOUND),
    summary="Show one conversation with its turns",
)
def get_ask_chat_thread(thread_id: str, request: Request) -> AskChatThreadDetailResponse:
    """The thread and its turns in order, each with its grounding and citations.

    Each turn also carries its stored passages, so a reload rebuilds the sources
    panel from the interaction snapshot instead of issuing a request per answer
    (FR-007). The texts are the full chunks, because that is what the vote's rank
    resolves to.
    """
    user = current_user(request)
    settings = get_settings(request)
    thread = _owned_thread(settings, thread_id, user)
    turns = ask_chat.thread_turns(settings.product_db_path, thread_id, user.id)
    ids = [turn.interaction_id for turn in turns if turn.interaction_id]
    names = document_filenames(settings.product_db_path, _doc_ids(settings, ids))
    return AskChatThreadDetailResponse(
        thread=_thread_response(thread),
        turns=[
            _turn_response(
                turn,
                _stored_passages(settings, turn, names),
            )
            for turn in turns
        ],
    )


def _doc_ids(settings: Settings, interaction_ids: list[str]) -> list[str]:
    """Every document id the turns' hits name, so the filenames are one query."""
    ids: list[str] = []
    for interaction_id in interaction_ids:
        stored = get_interaction(settings.product_db_path, interaction_id)
        if stored is not None:
            ids.extend(hit.doc_id for hit in stored.hits)
    return ids


def _stored_passages(
    settings: Settings, turn: ask_chat.Turn, names: dict[str, str]
) -> list[AskChatPassageResponse]:
    """One turn's stored hits, in rank order, as the panel reads them."""
    if not turn.interaction_id:
        return []
    stored = get_interaction(settings.product_db_path, turn.interaction_id)
    if stored is None:
        return []
    return [
        AskChatPassageResponse(
            rank=hit.rank,
            doc_id=hit.doc_id,
            filename=names.get(hit.doc_id, hit.doc_id),
            text=hit.text,
            score=hit.score,
        )
        for hit in stored.hits
    ]


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
    """Rename a conversation.

    The title is trimmed first and measured after: one to 120 characters once
    surrounding whitespace is gone. A title that is empty after trimming, or
    longer than 120 then, is a 400 and the stored title is unchanged.
    """
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
    """Remove the thread and its turns. The interactions and votes behind them stay (D-9).

    A delete is idempotent for the member who owns the thread: the second call
    answers 204 exactly as the first did, because "it is gone" is the outcome the
    caller asked for. An id that was never this member's — another member's, or one
    that never existed — answers 404, so a delete is still not a way to probe for
    somebody else's ids. The two are told apart by a marker row written in the same
    transaction as the delete, not by the thread's absence.
    """
    user = current_user(request)
    settings = get_settings(request)
    try:
        ask_chat.delete_thread(settings.product_db_path, thread_id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=NO_THREAD_MESSAGE) from exc
    return Response(status_code=204)


@router.post(
    "/ask/chat/completions",
    operation_id="ask_chat_completion",
    response_class=StreamingResponse,
    responses={
        **error_responses(
            BAD_REQUEST,
            UNAUTHORIZED,
            NOT_FOUND,
            CONFLICT,
            TOO_MANY_REQUESTS,
            SERVICE_UNAVAILABLE,
            BAD_GATEWAY,
        ),
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
    regenerate_of = _regenerate_target(history, payload.regenerate)

    coordinator: IndexCoordinator = request.app.state.index
    # A malformed or over-long request is refused before anything about the
    # deployment is checked: it is the member's message that is wrong, and a
    # missing index must not mask a 400.
    _check_message_length(settings, question)
    if not _index_ready(coordinator):
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=INDEX_PREPARING)

    # The limiter runs before any row is written, so a refusal is not itself a
    # turn and cannot ratchet itself into a longer ban (D-8, AC 7).
    refusal = ask_chat.check_rate_limit(
        settings.product_db_path,
        user.id,
        max_turns=settings.ASK_CHAT_MAX_TURNS_PER_WINDOW,
        window_seconds=settings.ASK_CHAT_TURN_WINDOW_SECONDS,
        max_concurrent=settings.ASK_CHAT_MAX_CONCURRENT_TURNS,
    )
    if refusal is not None:
        raise HTTPException(
            status_code=TOO_MANY_REQUESTS,
            detail=refusal.message,
            headers={"Retry-After": str(refusal.retry_after)},
        )

    registry = registry_for(request.app.state)
    previous: ask_chat.Turn | None = None
    if regenerate_of is not None:
        # A regenerate replaces the answer it names rather than adding one beside
        # it: the turn keeps its id and seq, its votes are cleared, and its answer
        # text is rewritten (FR-015).
        try:
            previous = ask_chat.get_turn(settings.product_db_path, regenerate_of, user.id)
            turn = ask_chat.reopen_turn(
                settings.product_db_path, regenerate_of, user.id, question=question
            )
        except ask_chat.TurnBusy as exc:
            raise HTTPException(
                status_code=CONFLICT, detail=ask_chat_turn.ALREADY_RUNNING
            ) from exc
        except KeyError as exc:
            raise HTTPException(
                status_code=BAD_REQUEST, detail=NOTHING_TO_REGENERATE_MESSAGE
            ) from exc
        ask_chat.clear_turn_votes(settings.product_db_path, previous)
        history = [item for item in history if item.turn_id != regenerate_of]
    else:
        turn = ask_chat.open_turn(
            settings.product_db_path,
            thread.thread_id,
            user.id,
            question=question,
        )

    # The interaction is opened before the first byte, so a cancelled or failed
    # turn keeps the passages it found and the text the member had read (D-4).
    # A regenerate reuses the row the turn already has: one assistant turn holds
    # exactly one interaction, and the previous answer is overwritten in place.
    if previous is not None and previous.interaction_id:
        interaction_id = previous.interaction_id
        update_answer(settings.product_db_path, interaction_id, "")
    else:
        interaction_id = ask_chat.open_turn_interaction(
            settings.product_db_path,
            turn,
            question=question,
            config_path=_config_path(settings),
            user_email=user.email,
        )
    ask_chat.link_interaction(settings.product_db_path, turn.turn_id, user.id, interaction_id)
    live = registry.open(turn.turn_id, thread.thread_id)
    # The turn runs in a task the process owns, not in this request. Starlette
    # closes the relay generator when the socket closes — a reload, a navigation —
    # and that close used to cancel the model call itself, so the member came back
    # to a question with no answer. The task outlives the request, keeps storing
    # its text, and a resume subscribes to the same queue (FR-016).
    subscription = registry.subscribe(turn.turn_id)
    if subscription is None:  # pragma: no cover - just opened, so it is live
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=ask_chat_turn.ALREADY_RUNNING)
    _, queue = subscription
    live.task = asyncio.create_task(
        _produce(
            request.app.state,
            settings=settings,
            user=user,
            thread=thread,
            turn=turn,
            question=question,
            history=history,
            interaction_id=interaction_id,
        )
    )
    return StreamingResponse(
        _relay(request.app.state, turn_id=turn.turn_id, queue=queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            chat_stream.STREAM_HEADER: chat_stream.STREAM_HEADER_VALUE,
        },
    )


async def _relay(
    app_state: object,
    *,
    turn_id: str,
    queue: asyncio.Queue[str | None],
) -> AsyncIterator[str]:
    """Forward a turn the process owns, and stop forwarding when the reader goes.

    The model call runs in a task of its own (``_produce``), so a reload — which
    closes this generator — costs the member the rest of the stream they were
    reading and nothing else: the turn keeps running, keeps storing its text, and
    a subscribe on the resume route picks up where it was (FR-016). Before this,
    the relay drove the model itself and Starlette's close cancelled it, so a
    reload stored an empty answer and the member came back to a question with no
    reply.
    """
    registry = registry_for(app_state)
    try:
        while True:
            frame = await chat_stream.next_keepalive(queue)
            if frame is None:
                break
            yield frame
    finally:
        # Leaving without cancelling the task is the whole point: Stop is an
        # explicit request (``POST /ask/chat/turns/{id}/cancel``), not a closed
        # socket, because the two are indistinguishable here.
        registry.unsubscribe(turn_id, queue)


async def _produce(
    app_state: object,
    *,
    settings: Settings,
    user: User,
    thread: ask_chat.Thread,
    turn: ask_chat.Turn,
    question: str,
    history: list[ask_chat.Turn],
    interaction_id: str,
) -> None:
    """Run one turn to its terminal frame, publishing every part to the registry.

    This is the request-independent half of a turn: it owns the trace, the model
    loop, the stored text, and the settle. It is started with ``asyncio.create_task``
    and lives until the turn ends, whatever happens to the HTTP request that asked
    for it.
    """
    registry = registry_for(app_state)
    with turn_trace(
        interaction_id,
        {"question": question, "thread_id": thread.thread_id, "turn_id": turn.turn_id},
        {"thread_id": thread.thread_id, "turn_id": turn.turn_id},
    ) as trace:
        try:
            await _run_turn(
                app_state,
                settings=settings,
                user=user,
                thread=thread,
                turn=turn,
                question=question,
                history=history,
                interaction_id=interaction_id,
                trace=trace,
                publish=lambda frame: registry.publish(turn.turn_id, frame),
            )
        finally:
            # The live window closes after the terminal frames have been published,
            # so a subscriber already attached reads them in order and a resume that
            # arrives mid-turn tails to the end rather than being cut short (FR-016).
            # Closing here, not in the turn body, is what wakes those subscribers
            # when the task is cancelled.
            registry.close(turn.turn_id)


async def _run_turn(
    app_state: object,
    *,
    settings: Settings,
    user: User,
    thread: ask_chat.Thread,
    turn: ask_chat.Turn,
    question: str,
    history: list[ask_chat.Turn],
    interaction_id: str,
    trace: TurnTrace,
    publish: Callable[[str], None],
) -> None:
    """Run the turn, publishing its parts and settling its row on every exit path.

    The answer text is written on every delta, so a resume replays exactly what
    the browser had read and a cancelled turn keeps its words (D-4). The terminal
    ``data-deepfix-turn`` is emitted after the row is settled, so a resume and the
    browser agree about the status.

    Parts are published rather than yielded, and the terminal frames are published
    from the ``finally``. A cancelled task must still tell its subscribers the turn
    is over: a generator that raises at a ``yield`` cannot emit the closing frames,
    which left a resume waiting forever for a turn that had already stopped.
    """
    turn_id = turn.turn_id
    outcome: ask_chat_turn.TurnOutcome | None = None
    status = "failed"
    failure: str | None = None
    seen_text: list[str] = []
    trace_id = trace.trace_id
    # The searches this turn has completed so far, and the verdict and query they
    # imply. A cancelled or failed turn records these, so the evidence it kept is
    # readable the same way a completed turn's is (D-4, FR-006).
    live_calls: list[ask_chat_turn.ToolCall] = []
    live_grounding: list[str | None] = [None]
    live_query: list[str | None] = [None]
    # When the first visible character arrived. `_settle` used to read this from
    # the outcome, which does not exist after a cancel, so every stopped turn stored
    # NULL and an operator could not tell a slow provider from a broken one.
    first_token_ms: list[int | None] = [None]
    started = time.monotonic()

    def store_hits(calls: list[ask_chat_turn.ToolCall]) -> None:
        """Persist the hits as each search completes, so a stop keeps the evidence."""
        seen: set[tuple[str, str]] = set()
        results = []
        for call in calls:
            for result in call.result.results:
                key = (result.doc_id, result.text)
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)
        replace_interaction(
            settings.product_db_path,
            interaction_id,
            answer="".join(seen_text),
            results=results,
            build_id=calls[-1].result.build_id or "",
            trace_id=trace_id,
        )
        latest = calls[-1]
        # One child span per call, carrying the numbers that made it useful:
        # the query, how many passages survived, the best score, and the latency.
        trace.tool_call(
            query=latest.result.query,
            passage_count=len(latest.result.passages),
            max_score=latest.result.max_score,
            duration_ms=latest.duration_ms,
        )
        # Remember what the searches found, so a turn that is cancelled or fails
        # from here records the same verdict and query a completed one would.
        live_calls[:] = calls
        live_grounding[0] = ask_chat_turn.partial_grounding(calls, history, "".join(seen_text))
        live_query[0] = ask_chat_turn.last_query(calls)

    publish(chat_stream.sse(chat_stream.start_part(turn_id)))
    # The text group's id, and whether it is open. A cancel lands between a
    # `text-start` and its `text-end`, so the closing frame has to be synthesised
    # from here; without it the runtime keeps the part `streaming` and draws
    # nothing at all, which is what a stopped turn used to look like (D-4).
    open_text_id: list[str | None] = [None]
    try:
        async for produced in ask_chat_turn.run_turn(
            coordinator=app_state.index,
            settings=settings,
            history=history,
            question=question,
            on_tool_call=store_hits,
        ):
            if isinstance(produced, ask_chat_turn.TurnOutcome):
                outcome = produced
                continue
            kind = produced.get("type")
            if kind == "text-start":
                open_text_id[0] = str(produced.get("id") or "")
            elif kind == "text-end":
                open_text_id[0] = None
            elif kind == "text-delta":
                if first_token_ms[0] is None:
                    first_token_ms[0] = int((time.monotonic() - started) * 1000)
                seen_text.append(str(produced.get("delta") or ""))
                # Written per delta, so a resume and a cancel both see what was read.
                update_answer(settings.product_db_path, interaction_id, "".join(seen_text))
            publish(chat_stream.sse(produced))
        status = "completed"
    except asyncio.CancelledError:
        # Stop cancelled this task, or the loop is shutting down. The row is
        # settled below and the partial text stays readable (D-4, FR-014).
        status = "cancelled"
        failure = ask_chat_turn.CANCELLED
        raise
    except ask_chat_turn.TurnTimedOut as exc:
        # The turn's wall clock ran out. It is a failure of the turn, not the
        # member's decision to stop, so it settles `failed` with the reason and the
        # member is told it timed out (FR-017, D-12). It is published as an error
        # part too, because the stream is still open and a silent truncation would
        # read as a finished answer.
        failure = str(exc) or ask_chat_turn.TOO_LONG
        publish(chat_stream.sse(chat_stream.error_part(failure)))
    except ask_chat_turn.TurnCancelled:
        status = "cancelled"
        failure = ask_chat_turn.CANCELLED
    except ask_chat_turn.TurnFailed as exc:
        failure = str(exc)
        publish(chat_stream.sse(chat_stream.error_part(failure)))
    except ask_chat.IndexNotReadyError as exc:
        failure = str(exc)
        publish(chat_stream.sse(chat_stream.error_part(failure)))
    except Exception:
        LOGGER.exception("ask chat turn %s failed", turn_id)
        failure = ask_chat_turn.UPSTREAM_FAILED
        publish(chat_stream.sse(chat_stream.error_part(failure)))
    finally:
        # One owner for the terminal state, on every path including a cancel:
        # nothing may leave the row `running` (FR-017, NFR-1). The terminal frames
        # are published here rather than after the loop because a cancelled task
        # never reaches code past the `await` it was cancelled at, and a subscriber
        # waiting for the turn to end must still be told it ended.
        stored = _settle(
            settings,
            user=user,
            turn=turn,
            question=question,
            status=status,
            failure=failure,
            outcome=outcome,
            interaction_id=interaction_id,
            partial="".join(seen_text),
            trace_id=trace_id,
            trace=trace,
            live_grounding=live_grounding[0],
            live_query=live_query[0],
            first_token_ms=first_token_ms[0],
        )
        _publish_terminal(
            publish,
            turn_id,
            status,
            outcome,
            stored,
            open_text_id=open_text_id[0],
        )


def _publish_terminal(
    publish: Callable[[str], None],
    turn_id: str,
    status: str,
    outcome: ask_chat_turn.TurnOutcome | None,
    stored: ask_chat.Turn,
    *,
    open_text_id: str | None = None,
) -> None:
    """Send the frames that end a turn.

    ``stored`` is the settled row, so the verdict these frames carry is the one
    that was written rather than the one the loop was about to write.

    ``open_text_id`` closes a text group the cancelled step never closed. The
    frames go out in the order the runtime expects — ``text-end``, then the
    grounding part, then the turn and ``finish`` — so the page can draw the
    stopped badge without a reload (D-4, FR-014).
    """
    if open_text_id:
        publish(chat_stream.sse(chat_stream.text_end(open_text_id)))
    grounding = stored.grounding or Grounding.NO_RELEVANT_SOURCES.value
    citations = [
        {"marker": c.marker, "rank": c.rank, "doc_id": c.doc_id} for c in stored.citations
    ]
    # The grounding part is sent for a settled turn even when the loop never
    # returned an outcome: a cancelled or failed turn that searched still reached a
    # verdict, and that verdict is what the badge is drawn from. It is not sent for
    # a turn still `running`, because there is nothing to claim yet.
    # The grounding part and the terminal frame must agree about whether there is a
    # verdict, or the page draws one and the store says another. A settled turn
    # always has one; a turn still running has nothing to claim.
    has_verdict = stored.status != "running" and stored.grounding is not None
    if has_verdict:
        publish(
            chat_stream.sse(
                chat_stream.grounding_part(
                    grounding,
                    citations,
                    max_score=outcome.max_score if outcome is not None else 0.0,
                    cutoff=outcome.cutoff if outcome is not None else 0.0,
                )
            )
        )
    publish(
        chat_stream.sse(
            chat_stream.turn_part(
                turn_id,
                status,
                grounding=grounding if has_verdict else None,
                citations=citations,
            )
        )
    )
    publish(chat_stream.sse(chat_stream.finish_part("stop" if status == "completed" else "error")))
    publish(chat_stream.DONE_FRAME)


def _settle(
    settings: Settings,
    *,
    user: User,
    turn: ask_chat.Turn,
    question: str,
    status: str,
    failure: str | None,
    outcome: ask_chat_turn.TurnOutcome | None,
    interaction_id: str,
    partial: str,
    trace_id: str | None,
    trace: TurnTrace,
    live_grounding: str | None = None,
    live_query: str | None = None,
    first_token_ms: int | None = None,
) -> ask_chat.Turn:
    """Finish the turn's one ``interactions`` row and its terminal state.

    The row was opened before the first byte so a cancelled or failed turn can be
    read at all; this fills in the final answer, its hits, and the tool's query
    without ever adding a second row for the turn (FR-019). It goes through
    ``interactions.replace_interaction`` so votes, history, the MLflow assessment
    mirror, and the optimizer's export keep reading one shape.

    A turn with no outcome still gets its **validated citations**, from the text it
    did write and the passages it did find. Leaving them empty made every stopped
    turn render "Partly grounded": the verdict says `grounded`, the citations are
    absent, and the badge rule reads that pair as an uncited answer. The citations
    were there all along — they just were not being computed for this path (D-4).
    """
    if outcome is not None:
        replace_interaction(
            settings.product_db_path,
            interaction_id,
            answer=outcome.answer,
            results=outcome.retrieval_results,
            build_id=outcome.build_id or "",
            trace_id=trace_id,
        )
        update_answer(settings.product_db_path, interaction_id, outcome.answer)
        citations = _citations(outcome)
    else:
        # A turn with no outcome keeps the question, whatever text was read, and —
        # if it managed any searches before it stopped — the passages they found.
        # The evidence is real, so a resume and a later vote see what a completed
        # turn would have seen (FR-006, D-4).
        update_answer(settings.product_db_path, interaction_id, partial)
        set_trace_id(settings.product_db_path, interaction_id, trace_id)
        citations = _partial_citations(settings, partial, interaction_id)
    # The trace's terminal half: what it produced and how it ended, so a cancelled
    # or failed turn is accounted for too (FR-020, AC 5).
    grounding = outcome.grounding if outcome else live_grounding
    trace.outputs(
        {
            "status": status,
            "answer": outcome.answer if outcome else partial,
            "grounding": grounding,
        }
    )
    trace.tag("status", status)
    trace.tag("grounding", grounding)
    if status == "failed":
        # The trace's own state, not just a tag: a failed turn must not read as a
        # healthy trace when a reviewer filters by state.
        trace.failed(failure or "The turn failed.")
    return ask_chat.finish_turn(
        settings.product_db_path,
        turn.turn_id,
        user.id,
        status=status,
        error=failure,
        grounding=outcome.grounding if outcome else live_grounding,
        citations=citations,
        retrieval_query=outcome.retrieval_query if outcome else live_query,
        first_token_ms=outcome.first_token_ms if outcome is not None else first_token_ms,
    )


def _citations(outcome: ask_chat_turn.TurnOutcome) -> list[ask_chat.Citation]:
    return [
        ask_chat.Citation(
            marker=int(item["marker"]), rank=int(item["rank"]), doc_id=str(item["doc_id"])
        )
        for item in outcome.citations
    ]


def _partial_citations(
    settings: Settings, partial: str, interaction_id: str
) -> list[ask_chat.Citation]:
    """The citations a stopped or failed turn can honestly claim.

    ``ask_chat_turn.valid_citations`` is the one validator, and it needs the
    passages the turn actually found — which are already stored against the
    interaction as its hits, written as each search completed. An empty answer has
    no markers, so this returns nothing and the turn stores none: a cancel before
    the first word is honestly uncited, and the web badge says so in its own words
    rather than calling it "Partly grounded" (D-4, D-10).
    """
    if not partial.strip():
        return []
    interaction = get_interaction(settings.product_db_path, interaction_id)
    if interaction is None:
        return []
    # `valid_citations` matches markers against a passage's rank, which is the one
    # field the stored hit and the delivered passage agree on. The other fields
    # (`filename`, `truncated`) are not in the hit row, so they are not invented
    # here; the helper only needs the ranks.
    passages = [
        ask_chat.Passage(
            rank=hit.rank,
            doc_id=hit.doc_id,
            filename="",
            text=hit.text,
            score=hit.score,
            truncated=False,
        )
        for hit in interaction.hits
    ]
    return [
        ask_chat.Citation(
            marker=int(item["marker"]), rank=int(item["rank"]), doc_id=str(item["doc_id"])
        )
        for item in ask_chat_turn.valid_citations(partial, passages)
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


def _check_message_length(settings: Settings, question: str) -> None:
    """A question longer than the bound is a 400 naming it, not a truncated prompt."""
    if len(question) > settings.ASK_CHAT_MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=BAD_REQUEST,
            detail=(
                f"A question must be at most {settings.ASK_CHAT_MAX_MESSAGE_CHARS} characters."
            ),
        )


def _regenerate_target(history: list[ask_chat.Turn], regenerate: bool) -> str | None:
    """The newest answered turn, which a regenerate replaces in place (FR-015).

    The AI SDK's reload drops the message it is replacing, so the browser cannot
    name the turn; the server resolves it from the store exactly as the admin
    chat's ``_regenerate_target`` does. A thread with nothing answered has nothing
    to replace, so it is a 400 rather than a silent no-op.
    """
    if not regenerate:
        return None
    # AC 13: a reply still being written is not replaced underneath itself. The
    # newest turn decides, because "regenerate" means "redo the last answer".
    newest = history[-1] if history else None
    if newest is not None and newest.status == "running":
        raise HTTPException(status_code=CONFLICT, detail=ask_chat_turn.ALREADY_RUNNING)
    # A failed turn is offered regenerate in the UI (AC 17), so it is a target too.
    answered = [turn for turn in history if turn.status in {"completed", "failed", "cancelled"}]
    if not answered:
        raise HTTPException(status_code=BAD_REQUEST, detail=NOTHING_TO_REGENERATE_MESSAGE)
    return answered[-1].turn_id


@router.post(
    "/ask/chat/turns/{turn_id}/cancel",
    operation_id="cancel_ask_chat_turn",
    status_code=204,
    responses={
        204: {"description": "The turn was stopped, or had already ended."},
        **error_responses(UNAUTHORIZED, NOT_FOUND),
    },
    summary="Stop a turn that is still being written",
)
def cancel_ask_chat_turn(turn_id: str, request: Request) -> Response:
    """Stop the model call for one of the caller's turns.

    Stop used to be a closed socket, which is indistinguishable from a reload —
    and a reload must *not* stop the answer, or the member loses the reply they
    were reading (FR-016). So stopping is an explicit request, and this is it:
    the running task is cancelled, which unwinds the relay, settles the row
    ``cancelled`` with the text read so far, and keeps the passages it found
    (FR-014, D-4).

    A turn that is not running here answers 204: it may have finished already,
    or it may be running in another worker, and neither is the caller's mistake.
    Another member's id is 404, never 403.
    """
    user = current_user(request)
    settings = get_settings(request)
    try:
        ask_chat.get_turn(settings.product_db_path, turn_id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=NO_TURN_MESSAGE) from exc
    live = registry_for(request.app.state).get(turn_id)
    if live is not None and live.task is not None and not live.task.done():
        live.task.cancel()
    return Response(status_code=204)


@router.get(
    "/ask/chat/turns/{turn_id}/stream",
    operation_id="resume_ask_chat_turn",
    response_class=StreamingResponse,
    responses={
        **error_responses(UNAUTHORIZED, NOT_FOUND),
        200: {
            "description": "The AI SDK UI message stream, replayed and then tailed.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
    summary="Resume an answer that is in progress or already finished",
)
def resume_ask_chat_turn(turn_id: str, request: Request) -> StreamingResponse:
    """Replay the stored answer, then tail the turn when it runs in this process.

    This is what a reload mid-answer calls, and the reason the reply survives it:
    the text is read from the turn's interaction and the status from the turn row,
    so a resume on another worker degrades to a replay instead of a blank stream
    (FR-016).
    """
    user = current_user(request)
    settings = get_settings(request)
    try:
        turn = ask_chat.get_turn(settings.product_db_path, turn_id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=NO_TURN_MESSAGE) from exc
    storage = settings.product_db_path
    return StreamingResponse(
        resume_frames(
            registry=registry_for(request.app.state),
            db_path=storage,
            turn=turn,
            displayed_floor=ask_chat.displayed_floor(request.app.state.index),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            chat_stream.STREAM_HEADER: chat_stream.STREAM_HEADER_VALUE,
        },
    )


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


def _turn_response(
    turn: ask_chat.Turn, passages: list[AskChatPassageResponse] | None = None
) -> AskChatTurnResponse:
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
        passages=passages or [],
        retrieval_query=turn.retrieval_query,
        first_token_ms=turn.first_token_ms,
        started_at=turn.started_at,
        ended_at=turn.ended_at,
        interaction_id=turn.interaction_id,
        question=turn.question,
        answer=turn.answer,
    )

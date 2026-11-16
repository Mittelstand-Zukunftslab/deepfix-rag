"""Live Ask turns, and the stream that replays and tails one.

A resume has two halves, exactly as the admin chat's ``chat_turns`` module does.
While the turn runs in this process, a subscriber receives the parts the relay is
producing. Once the process holding it is gone — a restart, or a second worker —
only the stored answer text exists, so a resume replays that and closes with the
stored status.

Two differences from the admin chat, both because an Ask turn's text lives in the
``interactions`` row rather than a message row:

* the stored text is read from the interaction the turn links to, and
* a resumed stream also replays the turn's grounding, because the badge has to be
  drawn from the store rather than from the part that was missed.

The API runs one uvicorn worker (PRD A4), so the registry is a plain ``dict``
behind a lock; the stored-text fallback is what makes a second worker degrade
instead of break.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
import threading

from deepfix_rag import ask_chat
from deepfix_rag.api import chat_stream


@dataclass
class LiveAskTurn:
    """One running turn: its subscribers, the task producing it, and its frames."""

    turn_id: str
    thread_id: str
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    closed: bool = False
    # The task running the model call. It is owned by the process rather than by
    # the request that started it, so a reload does not end the answer (FR-016).
    task: asyncio.Task[None] | None = None
    # Every frame published so far, in order. A resume replays this instead of
    # the stored text, so a late subscriber sees the whole answer exactly once:
    # replaying the store and then tailing would either lose the deltas published
    # between the two, or send the ones already in the store a second time.
    frames: list[str] = field(default_factory=list)


class AskTurnRegistry:
    """The Ask turns this process is currently relaying."""

    def __init__(self) -> None:
        self._turns: dict[str, LiveAskTurn] = {}
        self._lock = threading.Lock()

    def open(self, turn_id: str, thread_id: str) -> LiveAskTurn:
        """Register a running turn, before the model call starts."""
        live = LiveAskTurn(turn_id=turn_id, thread_id=thread_id)
        with self._lock:
            self._turns[turn_id] = live
        return live

    def get(self, turn_id: str) -> LiveAskTurn | None:
        """The live record for a turn, or ``None`` when it is not running here."""
        with self._lock:
            return self._turns.get(turn_id)

    def subscribe(self, turn_id: str) -> tuple[list[str], asyncio.Queue[str | None]] | None:
        """The frames so far and a queue for the rest, or ``None`` when not live here.

        Taking the backlog and the queue under the same lock is what makes a
        resume gapless: a frame is either already in the replay or it is coming
        through the queue, never neither and never both.
        """
        with self._lock:
            live = self._turns.get(turn_id)
            if live is None or live.closed:
                return None
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            live.subscribers.append(queue)
            return list(live.frames), queue

    def publish(self, turn_id: str, frame: str) -> None:
        """Send one frame to every subscriber of a running turn, and keep it."""
        with self._lock:
            live = self._turns.get(turn_id)
            subscribers = list(live.subscribers) if live is not None else []
            if live is not None and not live.closed:
                live.frames.append(frame)
        for queue in subscribers:
            queue.put_nowait(frame)

    def unsubscribe(self, turn_id: str, queue: asyncio.Queue[str | None]) -> None:
        with self._lock:
            live = self._turns.get(turn_id)
            if live is not None and queue in live.subscribers:
                live.subscribers.remove(queue)

    def close(self, turn_id: str) -> None:
        """End the turn's live window and wake every subscriber to stop."""
        with self._lock:
            live = self._turns.pop(turn_id, None)
            if live is None:
                return
            live.closed = True
            subscribers = list(live.subscribers)
            live.subscribers.clear()
        for queue in subscribers:
            queue.put_nowait(None)


def registry_for(state: object) -> AskTurnRegistry:
    """The app's Ask registry, created on first use."""
    registry = getattr(state, "ask_chat_turns", None)
    if isinstance(registry, AskTurnRegistry):
        return registry
    registry = AskTurnRegistry()
    setattr(state, "ask_chat_turns", registry)
    return registry


def stored_answer(db_path: Path, turn: ask_chat.Turn) -> str:
    """The answer text stored so far for one turn, or an empty string."""
    if not turn.interaction_id:
        return ""
    from deepfix_rag.interactions import get_interaction

    interaction = get_interaction(db_path, turn.interaction_id)
    return "" if interaction is None or interaction.answer is None else interaction.answer


async def resume_frames(
    *,
    registry: AskTurnRegistry,
    db_path: Path,
    turn: ask_chat.Turn,
    displayed_floor: float = 0.0,
) -> AsyncIterator[str]:
    """Replay the stored answer, then tail the live turn if it runs in this process.

    The stored text is sent exactly once, as one delta, so a reconnect cannot show
    a partial answer twice (FR-016). ``displayed_floor`` is the deployment's
    configured relevance floor, reported on the grounding part so a resumed answer
    carries the same number a live one did rather than a placeholder.

    While the turn runs in this process, its frames are replayed from the live
    record and then followed to the end. That is what makes a reload mid-answer
    show the rest of it: the turn never stopped, so the reader picks up the
    stream it left, and the stored text is not used — replaying both would show
    the opening of the answer twice.
    """
    turn_id = turn.turn_id
    text_id = f"{turn_id}-text"
    subscribed = registry.subscribe(turn_id)
    if subscribed is not None:
        published, queue = subscribed
        try:
            for frame in published:
                yield frame
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield frame
        finally:
            registry.unsubscribe(turn_id, queue)
        # The live record carried the terminal frames, so the replay is complete.
        return
    yield chat_stream.sse(chat_stream.start_part(turn_id))
    yield chat_stream.sse(chat_stream.step_start_part())
    yield chat_stream.sse(chat_stream.text_start(text_id))
    stored = stored_answer(db_path, turn)
    if stored:
        yield chat_stream.sse(chat_stream.text_delta(text_id, stored))
    # Re-read: a turn that was live has just settled, and the row is the truth.
    settled = ask_chat.get_turn(db_path, turn_id, turn.owner_id)
    yield chat_stream.sse(chat_stream.text_end(text_id))
    yield chat_stream.sse(chat_stream.step_finish_part())
    # A resumed turn reports the verdict the turn actually reached. A turn that
    # never searched has no grounding at all, so it reports none rather than
    # claiming "no relevant sources" — that phrase is a statement about a search,
    # and a failed or refused turn never made one.
    grounding = settled.grounding
    citations = [
        {"marker": citation.marker, "rank": citation.rank, "doc_id": citation.doc_id}
        for citation in settled.citations
    ]
    if settled.status != "running" and grounding is not None:
        yield chat_stream.sse(
            chat_stream.grounding_part(
                grounding,
                citations,
                max_score=0.0,
                # The floor the deployment is configured with, not a placeholder:
                # a member comparing two answers should see one number.
                cutoff=displayed_floor,
            )
        )
    yield chat_stream.sse(
        chat_stream.turn_part(turn_id, settled.status, grounding=grounding, citations=citations)
    )
    yield chat_stream.sse(
        chat_stream.finish_part("stop" if settled.status == "completed" else "error")
    )
    yield chat_stream.DONE_FRAME

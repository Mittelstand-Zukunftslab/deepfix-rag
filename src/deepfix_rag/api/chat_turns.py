"""The in-process registry of running chat turns, and the resume stream.

A resume has two halves. While the turn is running in this process, a live
subscriber receives the parts the relay is producing. Once the process holding
the turn is gone — a restart, or a second worker — only the stored text exists,
so the resume replays it and then closes with the stored status.

The API runs one uvicorn worker (PRD A4), so the registry is a plain
``dict`` guarded by a lock. The stored-text fallback is what makes the
multi-worker case degrade instead of break, and it is why no Redis appears here.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
import threading

from deepfix_rag import admin_chat


@dataclass
class LiveTurn:
    """One running turn's subscribers and the text they have been sent."""

    turn_id: str
    thread_id: str
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    closed: bool = False


class TurnRegistry:
    """The turns this process is currently relaying."""

    def __init__(self) -> None:
        self._turns: dict[str, LiveTurn] = {}
        self._lock = threading.Lock()

    def open(self, turn_id: str, thread_id: str) -> LiveTurn:
        """Register a running turn. Called before the upstream call starts."""
        live = LiveTurn(turn_id=turn_id, thread_id=thread_id)
        with self._lock:
            self._turns[turn_id] = live
        return live

    def get(self, turn_id: str) -> LiveTurn | None:
        with self._lock:
            return self._turns.get(turn_id)

    def subscribe(self, turn_id: str) -> asyncio.Queue[str | None] | None:
        """A queue that receives every part from now on, or ``None`` if not live."""
        with self._lock:
            live = self._turns.get(turn_id)
            if live is None or live.closed:
                return None
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            live.subscribers.append(queue)
            return queue

    def publish(self, turn_id: str, frame: str) -> None:
        """Send one frame to every subscriber of a running turn."""
        with self._lock:
            live = self._turns.get(turn_id)
            subscribers = list(live.subscribers) if live is not None else []
        for queue in subscribers:
            queue.put_nowait(frame)

    def unsubscribe(self, turn_id: str, queue: asyncio.Queue[str | None]) -> None:
        """Drop one subscriber. A turn's own close drops the rest."""
        with self._lock:
            live = self._turns.get(turn_id)
            if live is None:
                return
            if queue in live.subscribers:
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


def registry_for(state: object) -> TurnRegistry:
    """The app's registry, created on first use."""
    registry = getattr(state, "chat_turns", None)
    if isinstance(registry, TurnRegistry):
        return registry
    registry = TurnRegistry()
    setattr(state, "chat_turns", registry)
    return registry


async def resume_frames(
    *,
    registry: TurnRegistry,
    db_path: Path,
    turn_id: str,
    text_id: str,
) -> AsyncIterator[str]:
    """Replay the stored text, then tail the live turn if it is running here.

    The stored text is sent exactly once, as one delta, so a reconnect cannot
    show a partial answer twice.
    """
    from deepfix_rag.api import chat_stream

    yield chat_stream.sse(chat_stream.start_part(turn_id))
    queue = registry.subscribe(turn_id)
    stored = admin_chat.stored_assistant_text(db_path, turn_id)
    yield chat_stream.sse(chat_stream.text_start(text_id))
    if stored:
        yield chat_stream.sse(chat_stream.text_delta(text_id, stored))
    if queue is None:
        turn = admin_chat.turn_status(db_path, turn_id)
        yield chat_stream.sse(chat_stream.text_end(text_id))
        yield chat_stream.sse(chat_stream.turn_part(turn_id, turn.status, []))
        yield chat_stream.sse(
            chat_stream.finish_part("stop" if turn.status == "completed" else "error")
        )
        yield chat_stream.DONE_FRAME
        return
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame
    finally:
        registry.unsubscribe(turn_id, queue)
    turn = admin_chat.turn_status(db_path, turn_id)
    yield chat_stream.sse(chat_stream.text_end(text_id))
    yield chat_stream.sse(chat_stream.turn_part(turn_id, turn.status, []))
    yield chat_stream.sse(
        chat_stream.finish_part("stop" if turn.status == "completed" else "error")
    )
    yield chat_stream.DONE_FRAME

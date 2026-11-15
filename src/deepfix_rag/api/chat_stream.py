"""Translate Hermes's OpenAI chat stream into the AI SDK's UI message stream.

The browser never sees OpenAI ``chat.completion.chunk`` frames. The chat UI runs
on the Vercel AI SDK through Assistant UI's official adapter, so what leaves
deepfix-api is the AI SDK's UI message stream (``x-vercel-ai-ui-message-stream:
v1``), a sequence of small JSON parts:

* ``start`` opens the assistant message.
* ``text-start`` / ``text-delta`` / ``text-end`` carry the reply text.
* ``data-deepfix-tool`` carries a Hermes ``hermes.tool.progress`` event.
* ``data-deepfix-turn`` carries the turn id, its terminal status, and the action
  cards proposed in the reply.
* ``error`` carries a redacted failure sentence.
* ``finish`` closes the message, and ``data: [DONE]`` closes the stream.

Every part is built here rather than copied from the upstream object, which is
what makes FR-003 structural: there is no ``model`` field to delete, because the
translator never writes one. A frame that is not a JSON object is dropped, and
the caller logs it, because an opaque payload could carry the model id this
module exists to withhold.

The functions are pure: no HTTP, no database, no clock. ``tests
/test_admin_chat_routes.py`` drives them directly and over a real socket.
"""

import json
from typing import Any

STREAM_HEADER = "x-vercel-ai-ui-message-stream"
STREAM_HEADER_VALUE = "v1"
DONE_FRAME = "data: [DONE]\n\n"
KEEPALIVE_FRAME = ": keepalive\n\n"

# The parts the AI SDK's ``uiMessageChunkSchema`` accepts, by ``type``.
TOOL_PART = "data-deepfix-tool"
TURN_PART = "data-deepfix-turn"
GROUNDING_PART = "data-deepfix-grounding"

# Hermes' progress event can arrive at the top level of a chunk or inside the
# first choice's delta. Both are read, so a change of nesting on the image does
# not silently drop progress.
_TOOL_KEYS = ("hermes.tool.progress", "tool_progress")


def sse(event: dict[str, Any]) -> str:
    """One SSE frame carrying one JSON part."""
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def start_part(message_id: str) -> dict[str, Any]:
    """Open the assistant message. The id is the turn id, so a resume matches."""
    return {"type": "start", "messageId": message_id}


def text_start(text_id: str) -> dict[str, Any]:
    return {"type": "text-start", "id": text_id}


def text_delta(text_id: str, delta: str) -> dict[str, Any]:
    return {"type": "text-delta", "id": text_id, "delta": delta}


def text_end(text_id: str) -> dict[str, Any]:
    return {"type": "text-end", "id": text_id}


def tool_part(progress: dict[str, Any]) -> dict[str, Any]:
    """A Hermes tool-progress event, as a ``data-*`` part the UI can render."""
    return {"type": TOOL_PART, "data": progress}


def step_start_part() -> dict[str, Any]:
    """Open one model step. A tool call and the prose after it are separate steps."""
    return {"type": "start-step"}


def step_finish_part() -> dict[str, Any]:
    """Close one model step, before the next one's tool call or the final prose."""
    return {"type": "finish-step"}


def tool_input_start(tool_call_id: str, tool_name: str) -> dict[str, Any]:
    """Announce one tool call before its arguments are known."""
    return {"type": "tool-input-start", "toolCallId": tool_call_id, "toolName": tool_name}


def tool_input_available(
    tool_call_id: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """The call's arguments, once the model has finished writing them."""
    return {
        "type": "tool-input-available",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "input": arguments,
    }


def tool_output_available(tool_call_id: str, output: Any) -> dict[str, Any]:
    """The tool's result. The object is the tool's own output verbatim (D-2)."""
    return {"type": "tool-output-available", "toolCallId": tool_call_id, "output": output}


def grounding_part(
    grounding: str,
    citations: list[dict[str, Any]],
    *,
    max_score: float,
    cutoff: float,
) -> dict[str, Any]:
    """The turn's grounding verdict, its validated citations, and the floor it used (D-3)."""
    return {
        "type": GROUNDING_PART,
        "data": {
            "grounding": grounding,
            "citations": list(citations),
            "max_score": max_score,
            "cutoff": cutoff,
        },
    }


def turn_part(
    turn_id: str,
    status: str,
    actions: list[dict[str, Any]] | None = None,
    *,
    grounding: str | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The turn's terminal information, as a ``data-*`` part."""
    data: dict[str, Any] = {
        "turn_id": turn_id,
        "status": status,
        "actions": list(actions or []),
    }
    if grounding is not None:
        data["grounding"] = grounding
        data["citations"] = list(citations or [])
    return {"type": TURN_PART, "data": data}


def error_part(message: str) -> dict[str, Any]:
    return {"type": "error", "errorText": message}


def finish_part(reason: str = "stop") -> dict[str, Any]:
    return {"type": "finish", "finishReason": reason}


def read_chunk(payload: object, text_id: str) -> tuple[list[dict[str, Any]], str]:
    """One upstream ``chat.completion.chunk`` -> parts, and any text it carried.

    ``text_id`` names the text part so the browser accumulates into one message.
    The returned text is what the caller appends to the stored assistant text;
    the parts are what the caller forwards. A payload with neither text nor a
    progress event yields no parts.
    """
    if not isinstance(payload, dict):
        return [], ""
    parts: list[dict[str, Any]] = []
    text = ""
    choice = _first_choice(payload)
    delta = choice.get("delta") if isinstance(choice, dict) else None
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str) and content:
            text = content
            parts.append(text_delta(text_id, content))
    progress = _tool_progress(payload, delta if isinstance(delta, dict) else None)
    if progress is not None:
        parts.append(tool_part(progress))
    return parts, text


def _first_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}


def _tool_progress(payload: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any] | None:
    sources = (payload, delta or {})
    for source in sources:
        for key in _TOOL_KEYS:
            value = source.get(key)
            if isinstance(value, dict):
                return value
    return None


def finish_reason(payload: object) -> str | None:
    """The upstream ``finish_reason``, when this chunk carries one."""
    if not isinstance(payload, dict):
        return None
    choice = _first_choice(payload)
    value = choice.get("finish_reason")
    return value if isinstance(value, str) and value else None

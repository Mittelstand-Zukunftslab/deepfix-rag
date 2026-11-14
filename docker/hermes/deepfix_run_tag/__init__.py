"""Stamp ``deepfix.run_id`` on spans that hermes-otel exports.

hermes-otel's ``common_attributes`` are fixed when the gateway starts, and a
DeepFix run id changes every optimization. This plugin reads the id out of
the turn (the instructions say ``The DeepFix run id is <id>``) and sets that
one attribute. It does not copy the message, the tool arguments, or any
credential onto a span.

``content_capture`` stays ``off`` in the optimization profile, so the prompt
itself is not exported. The id is still on the span.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

_LABELED = re.compile(r"The DeepFix run id is ([A-Za-z0-9][A-Za-z0-9._-]{0,127})\b")
_RUN_FLAG = re.compile(r"--run-id(?:=|\s+)([A-Za-z0-9][A-Za-z0-9._-]{0,127})\b")
_RUN_IN_PATH = re.compile(r"(?:^|[\\/])runs[\\/]([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?=[\\/]|$)")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SECRET_ENVS = (
    "API_SERVER_KEY",
    "OPENROUTER_API_KEY",
    "S3_SECRET_ACCESS_KEY",
    "HERMES_S3_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
)
_ATTRIBUTE = "deepfix.run_id"
_run_id = ""
_attached = False


def _scrub_text(text: str) -> str:
    for name in _SECRET_ENVS:
        secret = os.environ.get(name, "").strip()
        if len(secret) >= 8 and secret in text:
            text = text.replace(secret, "[redacted]")
    return text


def _scrub_span(span: Any, run_id: str = "") -> None:
    """Set the run id and strip credentials from string attributes.

    hermes-otel records ``hermes.tool.command`` even when content capture is
    off. A command that contains a secret must not leave the process.
    """
    global _run_id
    try:
        present = dict(span.attributes or {})
    except Exception:
        present = {}
    chosen = run_id or _run_id or _run_id_from_attributes(present)
    if chosen:
        _run_id = chosen
    if chosen and _ATTRIBUTE not in present:
        try:
            span.set_attribute(_ATTRIBUTE, chosen)
        except Exception:
            pass
    for key, value in present.items():
        if not isinstance(value, str):
            continue
        cleaned = _scrub_text(value)
        if cleaned == value:
            continue
        try:
            span.set_attribute(key, cleaned)
        except Exception:
            continue


class _RunIdProcessor:
    """Copy the current DeepFix run id onto spans started after it is known."""

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        _scrub_span(span)

    def on_end(self, span: Any) -> None:
        return

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def register(ctx: Any) -> None:
    """Register after hermes-otel so its spans already exist when a hook fires."""
    _attach_processor()
    for name in ("on_session_start", "pre_llm_call", "pre_api_request", "pre_tool_call"):
        ctx.register_hook(name, _on_turn)


def reset_state() -> None:
    """Forget the run id. Tests call this between cases."""
    global _run_id, _attached
    _run_id = ""
    _attached = False


def _on_turn(**kwargs: Any) -> None:
    found = _find_run_id(kwargs) or _marker_run_id()
    explicit = os.environ.get("DEEPFIX_RUN_ID", "").strip()
    if _acceptable(explicit):
        found = explicit
    if not found:
        _attach_processor()
        return
    global _run_id
    _run_id = found
    _attach_processor()
    _stamp_open_spans(str(kwargs.get("session_id") or ""), found)


def _stamp_open_spans(session_id: str, run_id: str, tracer: Any = None) -> None:
    if tracer is None:
        tracer = _tracer()
    if tracer is None:
        return
    spans = getattr(tracer, "spans", None)
    if spans is None:
        return
    for getter in ("get_session_root", "get_current_parent"):
        method = getattr(spans, getter, None)
        if method is None:
            continue
        try:
            span = method(session_id)
        except Exception:
            continue
        if span is None:
            continue
        _scrub_span(span, run_id)


def _providers() -> list[Any]:
    """Tracer providers that can take a processor.

    hermes-otel keeps its provider on the pipeline. The global provider is
    the fallback when that pipeline is not the one the process exports.
    """
    found: list[Any] = []
    tracer = _tracer()
    pipeline = getattr(tracer, "pipeline", None) if tracer is not None else None
    provider = getattr(pipeline, "tracer_provider", None) if pipeline is not None else None
    if provider is not None:
        found.append(provider)
    try:
        from opentelemetry import trace

        current = trace.get_tracer_provider()
    except Exception:
        current = None
    if current is not None and current not in found and hasattr(current, "add_span_processor"):
        found.append(current)
    return found


def _attach_processor() -> None:
    global _attached
    if _attached:
        return
    providers = _providers()
    if not providers:
        return
    try:
        from opentelemetry.sdk.trace import SpanProcessor
    except Exception:
        return

    class _SdkProcessor(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            _RunIdProcessor().on_start(span, parent_context)

        def on_end(self, span: Any) -> None:
            return

    attached = False
    for provider in providers:
        add = getattr(provider, "add_span_processor", None)
        if add is None:
            continue
        try:
            add(_SdkProcessor())
        except Exception:
            continue
        attached = True
    _attached = attached


def _tracer() -> Any:
    try:
        from hermes_otel.tracer import get_tracer
    except Exception:
        return None
    try:
        return get_tracer()
    except Exception:
        return None


def _find_run_id(value: Any, depth: int = 0) -> str | None:
    if depth > 6:
        return None
    if isinstance(value, str):
        return _run_id_in_text(value)
    if isinstance(value, dict):
        for key in ("user_message", "message", "command", "input"):
            if key in value:
                found = _find_run_id(value[key], depth + 1)
                if found:
                    return found
        for key, item in value.items():
            if key in {"messages", "request", "args", "result", "tools", "conversation_history"}:
                found = _find_run_id(item, depth + 1)
                if found:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_run_id(item, depth + 1)
            if found:
                return found
    return None


def _run_id_in_text(text: str) -> str | None:
    if len(text) > 100_000:
        text = text[:20_000]
    labeled = _LABELED.search(text)
    if labeled and _acceptable(labeled.group(1)):
        return labeled.group(1)
    flagged = _RUN_FLAG.search(text)
    if flagged and _acceptable(flagged.group(1)):
        return flagged.group(1)
    match = _RUN_IN_PATH.search(text)
    if match and _acceptable(match.group(1)):
        return match.group(1)
    return None


def _run_id_from_attributes(attributes: dict[str, Any]) -> str | None:
    """The run id already present on a span, usually in the terminal command."""
    for value in attributes.values():
        if not isinstance(value, str):
            continue
        found = _run_id_in_text(value)
        if found:
            return found
    return None


def _marker_run_id() -> str | None:
    candidates = [
        os.environ.get("DEEPFIX_RUN_MARKER", "").strip(),
        "/opt/data/workspace/.deepfix-run-id",
    ]
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        candidates.append(str(Path(home) / "workspace" / ".deepfix-run-id"))
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if _acceptable(text):
                    return text
        except OSError:
            continue
    return None


def _acceptable(value: str) -> bool:
    if not value or value in {".", ".."} or _RUN_ID.fullmatch(value) is None:
        return False
    for name in _SECRET_ENVS:
        secret = os.environ.get(name, "").strip()
        if len(secret) >= 8 and secret in value:
            return False
    return True

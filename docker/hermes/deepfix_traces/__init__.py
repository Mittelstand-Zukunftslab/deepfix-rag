"""Hermes plugin that records agent and LLM spans for one optimization turn.

This file is loaded by the Hermes interpreter (Python 3.14). It does not
import deepfix. When that import is unavailable it runs
``/opt/deepfix/.venv/bin/python -m deepfix_rag.hermes_traces``, which is the
exporter. Request bodies stay in the hook and are not copied into the trace.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any

logger = logging.getLogger("deepfix_traces")

_RUN_IN_PATH = re.compile(r"(?:^|[\\/])runs[\\/]([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?=[\\/]|$)")
_LABELED = re.compile(r"The DeepFix run id is ([A-Za-z0-9][A-Za-z0-9._-]{0,127})\b")
_LOCK = threading.Lock()
_calls: dict[str, list[dict[str, Any]]] = {}
_run_ids: dict[str, str] = {}


def register(ctx: Any) -> None:
    """Register the four hooks Hermes accepts for a turn and its provider calls."""
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)


def reset_state() -> None:
    """Drop turns remembered by this process. Tests call this between cases."""
    with _LOCK:
        _calls.clear()
        _run_ids.clear()


def _on_pre_api_request(**kwargs: Any) -> None:
    try:
        _remember(kwargs)
        session = _session(kwargs)
        model = _model(kwargs)
        request_id = _request_id(kwargs)
        with _LOCK:
            _calls.setdefault(session, []).append({"model": model, "ok": None, "id": request_id})
    except Exception:
        logger.warning("deepfix-traces pre_api_request failed")


def _on_post_api_request(**kwargs: Any) -> None:
    try:
        _remember(kwargs)
        session = _session(kwargs)
        model = _model(kwargs)
        request_id = _request_id(kwargs)
        ok = not _failed(kwargs)
        with _LOCK:
            calls = _calls.setdefault(session, [])
            if (
                calls
                and calls[-1].get("ok") is None
                and (not request_id or calls[-1].get("id") == request_id)
            ):
                calls[-1]["ok"] = ok
                if model and not calls[-1].get("model"):
                    calls[-1]["model"] = model
                return
            calls.append({"model": model, "ok": ok, "id": request_id})
    except Exception:
        logger.warning("deepfix-traces post_api_request failed")


def _on_post_llm_call(**kwargs: Any) -> None:
    try:
        _remember(kwargs)
        _flush(_session(kwargs))
    except Exception:
        logger.warning("deepfix-traces post_llm_call failed")


def _on_session_end(**kwargs: Any) -> None:
    try:
        _remember(kwargs)
        _flush(_session(kwargs))
    except Exception:
        logger.warning("deepfix-traces on_session_end failed")


def _flush(session: str) -> None:
    with _LOCK:
        calls = _calls.pop(session, [])
        run_id = _run_ids.get(session, "")
    if not run_id:
        return
    payload = {
        "run_id": run_id,
        "session_id": session,
        "llm_calls": [
            {"model": str(item.get("model") or ""), "ok": bool(item.get("ok"))}
            for item in calls
            if isinstance(item, dict)
        ],
    }
    _export(payload)


def _export(payload: dict[str, Any]) -> None:
    try:
        from deepfix_rag.hermes_traces import export_turn
    except ImportError:
        _export_subprocess(payload)
        return
    try:
        export_turn(
            run_id=str(payload.get("run_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            llm_calls=list(payload.get("llm_calls") or []),
        )
    except Exception:
        logger.warning("deepfix-traces export failed")


def _export_subprocess(payload: dict[str, Any]) -> None:
    python = os.environ.get("DEEPFIX_PYTHON", "/opt/deepfix/.venv/bin/python")
    try:
        completed = subprocess.run(
            [python, "-m", "deepfix_rag.hermes_traces"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("deepfix-traces export failed")
        return
    if completed.returncode != 0:
        logger.warning("deepfix-traces export failed")


def _remember(kwargs: dict[str, Any]) -> None:
    found = _find_run_id(kwargs) or _marker_run_id()
    explicit = os.environ.get("DEEPFIX_RUN_ID", "").strip()
    if _is_run_id(explicit):
        found = explicit
    if not found:
        return
    with _LOCK:
        _run_ids[_session(kwargs)] = found


def _find_run_id(value: Any, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, str):
        return _run_id_in_text(value)
    if isinstance(value, dict):
        labeled = _find_labeled(value, depth + 1)
        if labeled:
            return labeled
        for item in value.values():
            found = _find_path(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        labeled = _find_labeled(value, depth + 1)
        if labeled:
            return labeled
        for item in value:
            found = _find_path(item, depth + 1)
            if found:
                return found
    return None


def _find_labeled(value: Any, depth: int) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, str):
        match = _LABELED.search(_clip(value))
        if match and _is_run_id(match.group(1)):
            return match.group(1)
        return None
    if isinstance(value, dict):
        for item in value.values():
            found = _find_labeled(item, depth + 1)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_labeled(item, depth + 1)
            if found:
                return found
    return None


def _find_path(value: Any, depth: int) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, str):
        match = _RUN_IN_PATH.search(_clip(value))
        if match and _is_run_id(match.group(1)):
            return match.group(1)
        return None
    if isinstance(value, dict):
        for item in value.values():
            found = _find_path(item, depth + 1)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_path(item, depth + 1)
            if found:
                return found
    return None


def _run_id_in_text(text: str) -> str | None:
    clipped = _clip(text)
    labeled = _LABELED.search(clipped)
    if labeled and _is_run_id(labeled.group(1)):
        return labeled.group(1)
    match = _RUN_IN_PATH.search(clipped)
    if match and _is_run_id(match.group(1)):
        return match.group(1)
    return None


def _clip(text: str) -> str:
    if len(text) <= 100_000:
        return text
    return text[:20_000] + "\n" + text[-2_000:]


def _marker_run_id() -> str | None:
    candidates = [
        os.environ.get("DEEPFIX_RUN_MARKER", "").strip(),
        "/opt/data/workspace/.deepfix-run-id",
    ]
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        candidates.append(str(Path(home) / "workspace" / _MARKER))
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if _is_run_id(text):
                    return text
        except OSError:
            continue
    return None


_MARKER = ".deepfix-run-id"


def _is_run_id(value: str) -> bool:
    if not value or value in {".", ".."} or len(value) > 128:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value))


def _session(kwargs: dict[str, Any]) -> str:
    for key in ("session_id", "task_id"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return "default"


def _request_id(kwargs: dict[str, Any]) -> str:
    value = kwargs.get("api_request_id")
    if isinstance(value, str):
        return value[:200]
    return ""


def _model(kwargs: dict[str, Any]) -> str:
    found = _model_in(kwargs, 0)
    return found[:200] if found else ""


def _model_in(value: Any, depth: int) -> str:
    if depth > 6:
        return ""
    if not isinstance(value, dict):
        return ""
    model = value.get("model")
    if isinstance(model, str) and model.strip() and len(model) <= 200 and "\n" not in model:
        return model.strip()
    for key, item in value.items():
        if key in {"messages", "input", "prompt", "content", "tools"}:
            continue
        found = _model_in(item, depth + 1)
        if found:
            return found
    return ""


def _failed(kwargs: dict[str, Any]) -> bool:
    if kwargs.get("error") or kwargs.get("error_message"):
        return True
    status = kwargs.get("status")
    return isinstance(status, str) and status.lower() in {"error", "failed"}

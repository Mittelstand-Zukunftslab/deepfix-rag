"""MLflow traces for one Hermes optimization turn.

The pinned Hermes image (v0.22.7) exports OTLP only for content-free gateway
monitoring, and that exporter reads ``monitoring.export.otlp.endpoint``. It
does not honor ``OTEL_EXPORTER_OTLP_ENDPOINT`` for an agent turn or an LLM
call, so this module is the exporter. The Hermes plugin shells out to it
with the deepfix virtualenv. Span attributes and tags carry the DeepFix run
id and never a credential.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
import sys
from typing import Any

import mlflow
from mlflow.entities import SpanType

HERMES_EXPERIMENT = "deepfix_hermes"
RUN_ID_TAG = "deepfix.run_id"
_SECRET_ENVS = (
    "API_SERVER_KEY",
    "OPENROUTER_API_KEY",
    "S3_SECRET_ACCESS_KEY",
    "HERMES_S3_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
)
_MIN_SECRET_LENGTH = 8


def export_turn(
    *,
    run_id: str,
    session_id: str = "",
    llm_calls: list[dict[str, Any]] | None = None,
    secrets: tuple[str, ...] | None = None,
) -> str:
    """Write one agent span and one LLM span per call. Return the trace id.

    The active MLflow experiment is restored afterwards. ``deepfix eval`` keeps
    using ``MLFLOW_DEV_RAG_EXPERIMENT``.
    """
    hidden = _env_secrets() if secrets is None else secrets
    checked_run = _scrub(run_id, hidden)[:250]
    if not checked_run:
        raise ValueError("run_id is empty")
    checked_session = _scrub(session_id, hidden)[:250]
    calls = _clean_calls(llm_calls or [], hidden)
    previous = _active_experiment_id()
    try:
        mlflow.set_experiment(HERMES_EXPERIMENT)
        with mlflow.start_span(name="hermes.agent", span_type=SpanType.AGENT) as agent:
            agent.set_inputs({"run_id": checked_run, "session_id": checked_session})
            agent.set_attribute(RUN_ID_TAG, checked_run)
            mlflow.update_current_trace(tags={RUN_ID_TAG: checked_run})
            for model, ok in calls:
                with mlflow.start_span(name="hermes.llm", span_type=SpanType.LLM) as llm:
                    llm.set_attribute(RUN_ID_TAG, checked_run)
                    llm.set_inputs({"model": model} if model else {})
                    llm.set_outputs({"ok": ok})
            agent.set_outputs({"llm_calls": len(calls)})
            trace_id = agent.trace_id
        mlflow.flush_trace_async_logging()
        return trace_id
    finally:
        if previous:
            try:
                mlflow.set_experiment(experiment_id=previous)
            except Exception:
                pass


def main() -> int:
    """Read one JSON turn from stdin and write its trace."""
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 1
    if not isinstance(payload, dict):
        return 1
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return 1
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        session_id = ""
    calls = payload.get("llm_calls")
    if not isinstance(calls, list):
        calls = []
    try:
        export_turn(run_id=run_id, session_id=session_id, llm_calls=calls)
    except Exception as exc:
        print(f"deepfix-traces export failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


def _active_experiment_id() -> str | None:
    try:
        from mlflow.tracking.fluent import _get_experiment_id

        return _get_experiment_id()
    except Exception:
        return None


def _env_secrets() -> tuple[str, ...]:
    found: list[str] = []
    for name in _SECRET_ENVS:
        value = os.environ.get(name, "").strip()
        if len(value) >= _MIN_SECRET_LENGTH and value not in found:
            found.append(value)
    found.sort(key=len, reverse=True)
    return tuple(found)


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return " ".join(text.split())


def _clean_calls(
    calls: list[dict[str, Any]],
    secrets: tuple[str, ...],
) -> list[tuple[str, bool]]:
    cleaned: list[tuple[str, bool]] = []
    for item in calls:
        if not isinstance(item, Mapping):
            continue
        model = item.get("model")
        model_text = _scrub(model, secrets)[:200] if isinstance(model, str) else ""
        ok = item.get("ok")
        cleaned.append((model_text, bool(ok)))
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())

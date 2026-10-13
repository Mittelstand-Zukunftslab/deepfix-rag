"""Run one OpenHands conversation for a workspace job.

Executed by the OpenHands interpreter, with no imports from the product package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import uuid

_DEFAULT_ITERATIONS = 15
_RUN_NAMESPACE = uuid.UUID("5b1c55a2-6a43-4c1e-9f3e-2f0f3f6f2d11")
_MAX_NUDGES = 2
_NUDGE = (
    "Please continue. When you are done, use the finish tool with your complete answer as "
    "its message. DO NOT ask for human help."
)


def main(argv: list[str]) -> int:
    """Read the job, run one agent, and write ``agent_output.json``."""
    if len(argv) != 2:
        print("Usage: openhands_runner.py <run_id>", file=sys.stderr)
        return 2
    run_id = argv[1]
    run_dir = Path.cwd() / "runs" / run_id
    output_path = run_dir / "agent_output.json"
    try:
        job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
        prompt = str(job["prompt"])
        model = str(job["model"])
        base_url = job.get("base_url") or None
        api_key = job.get("api_key") or None
        raw_limit = job.get("max_iterations", _DEFAULT_ITERATIONS)
        max_iterations = _DEFAULT_ITERATIONS if raw_limit is None else int(raw_limit)
        if max_iterations < 1:
            raise RuntimeError("max_iterations must be at least 1.")
        final_response = _run(run_dir, run_id, prompt, model, base_url, api_key, max_iterations)
        _write(output_path, {"final_response": final_response, "error": None})
        return 0
    except Exception as exc:
        _write(output_path, {"final_response": None, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1


def litellm_model(model: str, base_url: str | None) -> str:
    """Name the model the way LiteLLM routes it.

    OpenRouter model ids such as ``openai/gpt-6-luna-pro`` need the ``openrouter/``
    prefix, or LiteLLM sends them to the provider in the first segment.
    """
    host = urlparse(base_url).hostname if base_url else None
    if host and host.endswith("openrouter.ai") and not model.startswith("openrouter/"):
        return f"openrouter/{model}"
    return model


def conversation_id_for(run_id: str) -> uuid.UUID:
    """Stable conversation id, so MLflow traces for one run share a session."""
    return uuid.uuid5(_RUN_NAMESPACE, run_id)


def _run(
    run_dir: Path,
    run_id: str,
    prompt: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    max_iterations: int,
) -> str:
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    trace_keys = _export_trace_settings()
    from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
    from openhands.sdk.conversation import get_agent_final_response
    from openhands.sdk.event import ActionEvent, MessageEvent
    from openhands.sdk.event.conversation_error import ConversationErrorEvent
    from openhands.sdk.tool.builtins.finish import FinishAction
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool
    from pydantic import SecretStr

    # The tracer is set up on import; the terminal tool must not inherit OTLP settings.
    for key in trace_keys:
        os.environ.pop(key, None)

    llm = LLM(
        model=litellm_model(model, base_url),
        base_url=base_url,
        api_key=SecretStr(api_key) if api_key else None,
        usage_id="deepfix-optimizer",
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name, params={"terminal_type": "subprocess"}),
            Tool(name=FileEditorTool.name),
        ],
        agent_context=AgentContext(
            system_message_suffix=(
                "Run deepfix from the terminal for indexing, evaluation, and config changes. "
                f"Write candidate configs and candidate indexes only under runs/{run_id}/. "
                "No human is watching this run: never ask for confirmation. When the task is "
                "done, use the finish action. Its message is your final response, so put your "
                "complete answer there."
            ),
        ),
    )

    def finished_with_message_only(events: list[object]) -> bool:
        if any(isinstance(e, ActionEvent) and isinstance(e.action, FinishAction) for e in events):
            return False
        return any(isinstance(e, MessageEvent) and e.source == "agent" for e in events)

    conversation = Conversation(
        agent=agent,
        workspace=str(Path.cwd()),
        persistence_dir=str(run_dir / ".openhands"),
        conversation_id=conversation_id_for(run_id),
        max_iteration_per_run=max_iterations,
        visualizer=None,
        observability_metadata={"deepfix.run_id": run_id},
        observability_tags=["deepfix-optimization"],
        observability_span_name="deepfix_optimization",
    )
    try:
        conversation.send_message(prompt)
        for nudge in range(_MAX_NUDGES + 1):
            conversation.run()
            events = list(conversation.state.events)
            status = str(conversation.state.execution_status.value)
            if status != "finished" or nudge == _MAX_NUDGES:
                break
            if not finished_with_message_only(events):
                break
            conversation.send_message(_NUDGE)
        _log_usage(conversation.conversation_stats.get_combined_metrics())
    finally:
        conversation.close()
        _flush_traces()
    if status != "finished":
        detail = next(
            (
                event.detail
                for event in reversed(events)
                if isinstance(event, ConversationErrorEvent)
            ),
            None,
        )
        raise RuntimeError(detail or f"The agent stopped with status {status}.")
    response = get_agent_final_response(events)
    if not response:
        raise RuntimeError("The agent returned no final response.")
    return response


def _export_trace_settings() -> list[str]:
    """Turn the runner's private OTLP settings into ``OTEL_*`` for the SDK tracer."""
    endpoint = os.environ.pop("DEEPFIX_AGENT_OTLP_ENDPOINT", "")
    headers = os.environ.pop("DEEPFIX_AGENT_OTLP_HEADERS", "")
    if not endpoint:
        return []
    exported = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
    }
    if headers:
        exported["OTEL_EXPORTER_OTLP_HEADERS"] = headers
    os.environ.update(exported)
    return list(exported)


def _log_usage(metrics: object) -> None:
    usage = getattr(metrics, "accumulated_token_usage", None)
    cost = getattr(metrics, "accumulated_cost", 0.0) or 0.0
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    print(
        f"LLM usage: {prompt_tokens} prompt tokens, {completion_tokens} completion tokens, "
        f"cost ${cost:.4f}",
        file=sys.stderr,
    )


def _flush_traces() -> None:
    try:
        from lmnr import Laminar
    except ImportError:
        return
    if Laminar.is_initialized():
        Laminar.flush()


def _write(path: Path, payload: dict[str, str | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

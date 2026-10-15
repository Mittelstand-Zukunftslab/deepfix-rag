"""Run one OpenHands conversation for a workspace job.

Executed by the OpenHands interpreter, with no imports from the product package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse
import uuid

_DEFAULT_ITERATIONS = 15
_DEFAULT_GOAL_ROUNDS = 3
_RUN_NAMESPACE = uuid.UUID("5b1c55a2-6a43-4c1e-9f3e-2f0f3f6f2d11")


class GoalNotMet(RuntimeError):
    """The judge did not confirm the objective within the allowed audit rounds."""

    def __init__(self, goal: dict[str, Any]) -> None:
        missing = goal["missing"] or "The judge did not confirm the objective."
        super().__init__(f"The goal was not met after {goal['rounds']} audit rounds: {missing}")
        self.goal = goal


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
        max_iterations = _positive(job, "max_iterations", _DEFAULT_ITERATIONS)
        goal_rounds = _positive(job, "goal_rounds", _DEFAULT_GOAL_ROUNDS)
        final_response, goal = _run(
            run_dir, run_id, prompt, model, base_url, api_key, max_iterations, goal_rounds
        )
        _write(output_path, {"final_response": final_response, "error": None, "goal": goal})
        return 0
    except GoalNotMet as exc:
        _write(output_path, {"final_response": None, "error": str(exc), "goal": exc.goal})
        print(str(exc), file=sys.stderr)
        return 1
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


def system_message(run_id: str, max_iterations: int) -> str:
    """System message suffix that tells the agent its rules and its step budget."""
    return (
        "Run deepfix from the terminal for indexing, evaluation, and config changes. "
        f"Write candidate configs and candidate indexes only under runs/{run_id}/. "
        "No human is watching this run: never ask for confirmation. A reviewer "
        "checks your work only from this conversation, so show the command output "
        "that proves each requirement. "
        f"Step budget: each round has {max_iterations} agent steps, and a round that uses "
        "them all without calling finish fails the run. Plan for about 10 to 12 steps. "
        "Avoid commands with long silent periods, or run them with a timeout parameter "
        "set high enough: the terminal tool pauses a command after 30 seconds without "
        "output, and a pause costs a step. "
        "When the task is done, use the finish action. "
        "Its message is your final response, so put your complete answer there."
    )


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
    goal_rounds: int,
) -> tuple[str, dict[str, Any]]:
    """Pursue ``prompt`` as a goal that a judge LLM audits after every agent run.

    A run that ends in any status other than ``finished`` (iteration limit, stuck,
    error) stops the job instead of being judged.
    """
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    trace_keys = _export_trace_settings()
    from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
    from openhands.sdk.conversation import get_agent_final_response
    from openhands.sdk.conversation.goal import GoalController, GoalDone
    from openhands.sdk.event.conversation_error import ConversationErrorEvent
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool
    from pydantic import SecretStr

    # The tracer is set up on import; the terminal tool must not inherit OTLP settings.
    for key in trace_keys:
        os.environ.pop(key, None)

    def make_llm(usage_id: str) -> LLM:
        return LLM(
            model=litellm_model(model, base_url),
            base_url=base_url,
            api_key=SecretStr(api_key) if api_key else None,
            usage_id=usage_id,
        )

    llm = make_llm("deepfix-optimizer")
    judge_llm = make_llm("goal-judge")
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name, params={"terminal_type": "subprocess"}),
            Tool(name=FileEditorTool.name),
        ],
        agent_context=AgentContext(
            system_message_suffix=system_message(run_id, max_iterations),
        ),
    )
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
    controller = GoalController(prompt, judge_llm, max_iterations=goal_rounds)
    try:
        conversation.send_message(controller.start())
        while True:
            conversation.run()
            events = list(conversation.state.events)
            status = str(conversation.state.execution_status.value)
            if status != "finished":
                break
            step = controller.on_run_finished(_judge_view(events))
            done = isinstance(step, GoalDone)
            verdict = step.outcome.verdict if done else step.verdict
            _log_verdict(controller.iteration, goal_rounds, verdict)
            if done:
                break
            conversation.send_message(step.followup)
        _log_usage("Agent", conversation.conversation_stats.get_combined_metrics())
        _log_usage("Judge", judge_llm.metrics)
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
    outcome = step.outcome
    goal = {
        "status": outcome.status,
        "rounds": outcome.iterations,
        "score": outcome.verdict.score,
        "missing": outcome.verdict.missing,
    }
    if outcome.status != "complete":
        raise GoalNotMet(goal)
    response = get_agent_final_response(events)
    if not response:
        raise RuntimeError("The agent returned no final response.")
    return response, goal


def _judge_view(events: list[Any]) -> list[Any]:
    """Events for the goal judge, with each agent action written out as an assistant turn.

    The judge's transcript keeps only text content, so tool calls (the command that
    produced an output, and the finish message) would otherwise be invisible to it.
    """
    from openhands.sdk.event import ActionEvent, MessageEvent
    from openhands.sdk.llm import Message, TextContent

    def described(event: Any) -> Any:
        if not isinstance(event, ActionEvent) or event.action is None:
            return event
        args = event.action.model_dump(mode="json", exclude_none=True, exclude={"kind"})
        if event.tool_name == "finish":
            text = f"[finish] {args.get('message', '')}"
        else:
            text = f"[{event.tool_name}] {json.dumps(args, ensure_ascii=False)}"
        return MessageEvent(
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text=text)]),
        )

    return [described(event) for event in events]


def _positive(job: dict[str, Any], key: str, default: int) -> int:
    raw = job.get(key, default)
    value = default if raw is None else int(raw)
    if value < 1:
        raise RuntimeError(f"{key} must be at least 1.")
    return value


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


def _log_verdict(round_number: int, rounds: int, verdict: Any) -> None:
    missing = f", missing: {verdict.missing}" if verdict.missing else ""
    print(
        f"Goal audit {round_number}/{rounds}: complete={verdict.complete}, "
        f"score={verdict.score:.2f}{missing}",
        file=sys.stderr,
    )


def _log_usage(label: str, metrics: Any) -> None:
    usage = getattr(metrics, "accumulated_token_usage", None)
    cost = getattr(metrics, "accumulated_cost", 0.0) or 0.0
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    print(
        f"{label} LLM usage: {prompt_tokens} prompt tokens, "
        f"{completion_tokens} completion tokens, cost ${cost:.4f}",
        file=sys.stderr,
    )


def _flush_traces() -> None:
    try:
        from lmnr import Laminar
    except ImportError:
        return
    if Laminar.is_initialized():
        Laminar.flush()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

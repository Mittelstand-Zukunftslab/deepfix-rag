"""Run one Hermes conversation for a workspace job.

Executed by the Hermes interpreter, with no imports from the product package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

_GATEWAY_PORTS = {8642, 9119}
_DEFAULT_ITERATIONS = 15


def main(argv: list[str]) -> int:
    """Read the job, run one agent, and write ``agent_output.json``."""
    if len(argv) != 2:
        print("Usage: hermes_runner.py <run_id>", file=sys.stderr)
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
        _reject_gateway(base_url)
        final_response = _run(run_dir, run_id, prompt, model, base_url, api_key, max_iterations)
        _write(output_path, {"final_response": final_response, "error": None})
        return 0
    except Exception as exc:
        _write(output_path, {"final_response": None, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1


def _reject_gateway(base_url: object) -> None:
    if not isinstance(base_url, str) or not base_url:
        return
    if urlparse(base_url).port in _GATEWAY_PORTS:
        raise RuntimeError("The Hermes gateway is not used.")


def _run(
    run_dir: Path,
    run_id: str,
    prompt: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    max_iterations: int,
) -> str:
    os.environ["HERMES_HOME"] = str((run_dir / ".hermes").resolve())
    os.environ["TERMINAL_BACKEND"] = "local"
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key
    from run_agent import AIAgent

    agent = AIAgent(
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_mode="chat_completions",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        skip_background_review=True,
        max_iterations=max_iterations,
        enabled_toolsets=["terminal"],
        ephemeral_system_prompt=(
            "Use the terminal tool to run commands. "
            f"Write candidate configs and candidate indexes only under runs/{run_id}/."
        ),
    )
    result = agent.run_conversation(user_message=prompt, task_id=run_id)
    response = result.get("final_response") if isinstance(result, dict) else None
    if not isinstance(response, str) or not response:
        raise RuntimeError("The agent returned no final response.")
    return response


def _write(path: Path, payload: dict[str, str | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

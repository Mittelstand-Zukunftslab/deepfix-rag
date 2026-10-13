"""Start one Hermes agent in a child process and stop it when time runs out."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Mapping
from urllib.parse import urlparse

DEFAULT_TIMEOUT_SECONDS = 3 * 60 * 60
DEFAULT_ITERATIONS = 15
_GATEWAY_PORTS = frozenset({8642, 9119})
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PASSTHROUGH = (
    "DEEPFIX_LLM_BASE_URL",
    "DEEPFIX_LLM_API_KEY",
    "DEEPFIX_LLM_MODEL_NAME",
    "MLFLOW_TRACKING_URI",
    "DEEPFIX_SERVER_URL",
)


class AgentRunError(Exception):
    """The job is invalid and was not started."""


class HermesUnavailable(Exception):
    """The Hermes interpreter is not configured."""


@dataclass(frozen=True)
class AgentRunResult:
    """Outcome of one child-process conversation."""

    final_response: str | None
    error: str | None
    timed_out: bool
    returncode: int | None


def default_runner_script() -> Path:
    """Standalone runner executed by the Hermes interpreter."""
    return Path(__file__).resolve().parents[2] / "scripts" / "hermes_runner.py"


def openrouter_settings(source: Mapping[str, str]) -> tuple[str, str, str | None]:
    """Return the OpenRouter model, base URL, and key.

    The key is ``DEEPFIX_LLM_API_KEY``. The product ``LLM_API_KEY`` is not used.
    """
    model = source.get("DEEPFIX_LLM_MODEL_NAME") or "openai/gpt-6-luna-pro"
    base_url = source.get("DEEPFIX_LLM_BASE_URL") or "https://openrouter.ai/api/v1"
    api_key = source.get("DEEPFIX_LLM_API_KEY") or None
    return model, base_url, api_key


def build_child_env(source: Mapping[str, str], api_bin: Path) -> dict[str, str]:
    """Allowlisted environment for the agent child.

    ``PATH`` starts with the API virtualenv ``bin`` so ``deepfix`` is this
    revision. ``OPENROUTER_API_KEY`` is copied from ``DEEPFIX_LLM_API_KEY``.
    """
    env = {key: source[key] for key in _PASSTHROUGH if source.get(key)}
    openrouter_key = source.get("DEEPFIX_LLM_API_KEY")
    if openrouter_key:
        env["OPENROUTER_API_KEY"] = openrouter_key
    base_path = source.get("PATH", "")
    env["PATH"] = f"{api_bin}{os.pathsep}{base_path}" if base_path else str(api_bin)
    return env


def interpreter_imports_agent(interpreter: Path) -> bool:
    """True when this interpreter can import ``AIAgent`` from ``run_agent``."""
    if not interpreter.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", "from run_agent import AIAgent"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def run_agent_job(
    workspace: Path,
    run_id: str,
    *,
    prompt: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    interpreter: Path,
    max_iterations: int = DEFAULT_ITERATIONS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runner_script: Path | None = None,
    source_env: Mapping[str, str] | None = None,
    api_bin: Path | None = None,
    private_paths: tuple[str, ...] = (),
) -> AgentRunResult:
    """Write the job, run one agent child, and kill its process group on timeout."""
    if _RUN_ID.fullmatch(run_id) is None:
        raise AgentRunError("run_id must be a single path segment.")
    if max_iterations < 1:
        raise AgentRunError("max_iterations must be at least 1.")
    _reject_private(prompt, private_paths)
    _reject_private(model, private_paths)
    _reject_private(base_url or "", private_paths)
    _reject_gateway(base_url)
    if not interpreter.is_file():
        raise HermesUnavailable("Hermes is not set up on this server, so a run cannot start.")

    script = runner_script or default_runner_script()
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "prompt": prompt,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "max_iterations": max_iterations,
    }
    _reject_private(json.dumps(job), private_paths)
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")

    env = build_child_env(
        source_env if source_env is not None else os.environ, api_bin or _api_bin()
    )
    log_path = run_dir / "agent.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(interpreter), str(script), run_id],
            cwd=workspace,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            return AgentRunResult(
                final_response=None,
                error="Timed out",
                timed_out=True,
                returncode=None,
            )

    response, error = _read_output(run_dir / "agent_output.json")
    if returncode != 0 and not error:
        error = f"The agent runner exited with status {returncode}."
    return AgentRunResult(
        final_response=response,
        error=error,
        timed_out=False,
        returncode=returncode,
    )


def _api_bin() -> Path:
    return Path(sys.executable).parent


def _reject_private(text: str, private_paths: tuple[str, ...]) -> None:
    for secret in private_paths:
        if secret and secret in text:
            raise AgentRunError("The agent job must not include API-private paths.")


def _reject_gateway(base_url: str | None) -> None:
    if not base_url:
        return
    if urlparse(base_url).port in _GATEWAY_PORTS:
        raise AgentRunError("The Hermes gateway is not used.")


def _kill_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _read_output(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, "The agent runner did not write a result."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "The agent runner did not write a result."
    response = payload.get("final_response")
    error = payload.get("error")
    if response is not None and not isinstance(response, str):
        response = str(response)
    if error is not None and not isinstance(error, str):
        error = str(error)
    return response, error

"""Hermes child process: allowlisted environment, timeout, and one real turn."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from deepfix_rag.agent_process import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentRunError,
    HermesUnavailable,
    build_child_env,
    default_runner_script,
    interpreter_imports_agent,
    openrouter_settings,
    run_agent_job,
)
from deepfix_rag.config.settings import Settings

_DUMP = """
import json, os, sys
from pathlib import Path
run_id = sys.argv[1]
Path("env.json").write_text(json.dumps(dict(os.environ)), encoding="utf-8")
out = Path("runs") / run_id / "agent_output.json"
out.write_text(json.dumps({"final_response": "ok", "error": None}), encoding="utf-8")
"""

_SLEEP = """
import os, subprocess, sys, time
from pathlib import Path
run_id = sys.argv[1]
out = Path("runs") / run_id
child = subprocess.Popen(["sleep", "60"])
(out / "parent.pid").write_text(str(os.getpid()), encoding="utf-8")
(out / "child.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.unit
def test_openrouter_key_comes_from_deepfix_llm_api_key() -> None:
    model, base_url, api_key = openrouter_settings(
        {
            "DEEPFIX_LLM_API_KEY": "openrouter-key",
            "DEEPFIX_LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "DEEPFIX_LLM_MODEL_NAME": "openai/gpt-6-luna-pro",
            "LLM_API_KEY": "product-key",
            "OPENROUTER_API_KEY": "stale-key",
        }
    )
    assert model == "openai/gpt-6-luna-pro"
    assert base_url == "https://openrouter.ai/api/v1"
    assert api_key == "openrouter-key"
    assert Settings.model_fields["OPTIMIZATION_RUN_TIMEOUT_HOURS"].default == 3
    assert DEFAULT_TIMEOUT_SECONDS == 3 * 60 * 60


@pytest.mark.unit
def test_child_env_keeps_the_openrouter_key_and_drops_secrets(tmp_path: Path) -> None:
    api_bin = tmp_path / "bin"
    private = tmp_path / "private"
    source = {
        "PATH": "/usr/bin:/bin",
        "DEEPFIX_LLM_API_KEY": "openrouter-key",
        "DEEPFIX_LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "DEEPFIX_LLM_MODEL_NAME": "openai/gpt-6-luna-pro",
        "LLM_API_KEY": "product-key",
        "OPENROUTER_API_KEY": "stale-key",
        "MLFLOW_TRACKING_URI": "http://127.0.0.1:5000",
        "DEEPFIX_SERVER_URL": "http://127.0.0.1:8844",
        "PRODUCT_DATA_DIR": str(private),
        "SECRET_SESSION_TOKEN": "session-secret",
    }
    env = build_child_env(source, api_bin)
    assert env["PATH"].startswith(str(api_bin) + os.pathsep)
    assert env["DEEPFIX_LLM_API_KEY"] == "openrouter-key"
    assert env["OPENROUTER_API_KEY"] == "openrouter-key"
    assert "LLM_API_KEY" not in env
    assert "PRODUCT_DATA_DIR" not in env
    assert "SECRET_SESSION_TOKEN" not in env
    assert str(private) not in " ".join(env.values())
    assert "session-secret" not in " ".join(env.values())


@pytest.mark.unit
def test_runner_script_does_not_import_the_product_package() -> None:
    source = default_runner_script().read_text(encoding="utf-8")
    assert "deepfix_rag" not in source


@pytest.mark.integration
def test_job_uses_the_openrouter_key_and_hides_private_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "private"
    api_bin = tmp_path / "venv" / "bin"
    api_bin.mkdir(parents=True)
    script = tmp_path / "dump_runner.py"
    script.write_text(_DUMP, encoding="utf-8")
    source = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DEEPFIX_LLM_API_KEY": "openrouter-key",
        "DEEPFIX_LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "DEEPFIX_LLM_MODEL_NAME": "openai/gpt-6-luna-pro",
        "LLM_API_KEY": "product-key",
        "PRODUCT_DATA_DIR": str(private),
        "SECRET_SESSION_TOKEN": "session-secret",
    }
    prompt = "Run deepfix --help"
    result = run_agent_job(
        workspace,
        "run1",
        prompt=prompt,
        model="openai/gpt-6-luna-pro",
        base_url="https://openrouter.ai/api/v1",
        api_key="openrouter-key",
        interpreter=Path(sys.executable),
        timeout_seconds=30,
        runner_script=script,
        source_env=source,
        api_bin=api_bin,
        private_paths=(str(private), "session-secret"),
    )
    assert result.final_response == "ok"
    assert result.error is None
    job = json.loads((workspace / "runs" / "run1" / "job.json").read_text(encoding="utf-8"))
    assert job["prompt"] == prompt
    assert job["api_key"] == "openrouter-key"
    assert job["max_iterations"] == 15
    assert "product-key" not in json.dumps(job)
    assert str(private) not in json.dumps(job)
    child_env = json.loads((workspace / "env.json").read_text(encoding="utf-8"))
    assert child_env["OPENROUTER_API_KEY"] == "openrouter-key"
    assert "SECRET_SESSION_TOKEN" not in child_env
    assert "PRODUCT_DATA_DIR" not in child_env

    with pytest.raises(AgentRunError):
        run_agent_job(
            workspace,
            "run2",
            prompt=f"look in {private}",
            model="openai/gpt-6-luna-pro",
            base_url="https://openrouter.ai/api/v1",
            api_key="openrouter-key",
            interpreter=Path(sys.executable),
            runner_script=script,
            source_env=source,
            private_paths=(str(private),),
        )
    assert not (workspace / "runs" / "run2").exists()


@pytest.mark.integration
def test_runner_rejects_the_hermes_gateway(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "gate"
    run_dir.mkdir(parents=True)
    (run_dir / "job.json").write_text(
        json.dumps(
            {
                "prompt": "hi",
                "model": "m",
                "base_url": "http://127.0.0.1:8642/v1",
                "max_iterations": 1,
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(default_runner_script()), "gate"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    payload = json.loads((run_dir / "agent_output.json").read_text(encoding="utf-8"))
    assert "gateway" in payload["error"].lower()

    with pytest.raises(AgentRunError):
        run_agent_job(
            workspace,
            "gate2",
            prompt="hi",
            model="m",
            base_url="http://127.0.0.1:9119/v1",
            api_key=None,
            interpreter=Path(sys.executable),
        )


@pytest.mark.integration
def test_timeout_kills_the_process_group(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    script = tmp_path / "sleep_runner.py"
    script.write_text(_SLEEP, encoding="utf-8")
    result = run_agent_job(
        workspace,
        "slow",
        prompt="sleep",
        model="m",
        base_url="https://openrouter.ai/api/v1",
        api_key=None,
        interpreter=Path(sys.executable),
        timeout_seconds=2,
        runner_script=script,
        source_env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert result.timed_out is True
    assert result.error == "Timed out"
    run_dir = workspace / "runs" / "slow"
    parent = int((run_dir / "parent.pid").read_text(encoding="utf-8"))
    child = int((run_dir / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (_alive(parent) or _alive(child)):
        time.sleep(0.05)
    assert _alive(parent) is False
    assert _alive(child) is False


@pytest.mark.integration
def test_missing_interpreter_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(HermesUnavailable):
        run_agent_job(
            tmp_path / "workspace",
            "missing",
            prompt="hi",
            model="m",
            base_url=None,
            api_key=None,
            interpreter=tmp_path / "no-such-python",
        )


@pytest.mark.network
def test_agent_turn_runs_deepfix_help(tmp_path: Path) -> None:
    raw = os.environ.get("HERMES_PYTHON")
    interpreter = Path(raw) if raw else None
    if interpreter is None or not interpreter_imports_agent(interpreter):
        pytest.skip("Hermes interpreter is not configured or cannot import the agent library")
    spec = subprocess.run(
        [
            str(interpreter),
            "-c",
            "import importlib.util, sys; "
            "sys.exit(0 if importlib.util.find_spec('deepfix_rag') is None else 1)",
        ],
        capture_output=True,
        check=False,
    )
    assert spec.returncode == 0
    model, base_url, api_key = openrouter_settings(os.environ)
    assert api_key, "DEEPFIX_LLM_API_KEY is not set"
    prompt = (
        "Use the terminal tool to run exactly this command and nothing else: deepfix --help\n"
        "Copy that command's complete stdout into your final response, unchanged."
    )
    result = run_agent_job(
        tmp_path / "workspace",
        "help1",
        prompt=prompt,
        model=model,
        base_url=base_url,
        api_key=api_key,
        interpreter=interpreter,
        max_iterations=6,
        timeout_seconds=180,
        private_paths=(),
    )
    assert result.timed_out is False
    assert result.error is None
    assert result.final_response is not None
    assert "Usage: deepfix" in result.final_response

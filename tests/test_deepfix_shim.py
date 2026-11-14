"""The deepfix PATH shim, exercised as a real child process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import textwrap

import pytest

from deepfix_rag.deepfix_shim import HEARTBEAT_INTERVAL_SECONDS, _Redactor

pytestmark = [pytest.mark.unit]

SECRET = "scoped-secret-value"
BUCKET = "deepfix-index"


def _child(path: Path, *, sleep: str) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import os
            import sys
            import time
            secret = os.environ.get("S3_SECRET_ACCESS_KEY", "")
            sys.stdout.write("OUT " + secret + "\\n")
            sys.stdout.flush()
            sys.stderr.write("ERR " + secret + "\\n")
            sys.stderr.flush()
            time.sleep({sleep})
            raise SystemExit(3)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _env(tmp_path: Path, child: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DEEPFIX_REAL_BIN": str(child),
        "DEEPFIX_WORK_ROOT": str(tmp_path / "work"),
        "DEEPFIX_BUNDLE_ROOT": str(tmp_path / "objects"),
        "DEEPFIX_CONTAINER_LOG": str(tmp_path / "logs" / "deepfix-cli.log"),
        "S3_URI": f"s3://{BUCKET}/dev",
        "S3_SECRET_ACCESS_KEY": SECRET,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def test_shim_heartbeats_forwards_stdio_and_redacts_the_container_log(tmp_path: Path) -> None:
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0.55")
    config = tmp_path / "work" / "runs" / "run-77" / "input" / "champion.yaml"
    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "--config", str(config)],
        env=_env(tmp_path, child, DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="0.15"),
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 3
    assert completed.stdout == f"OUT {SECRET}\n".encode()
    assert f"ERR {SECRET}\n".encode() in completed.stderr
    heartbeats = [
        line for line in completed.stderr.splitlines() if line.startswith(b"deepfix-heartbeat")
    ]
    assert len(heartbeats) >= 2
    assert SECRET.encode() not in heartbeats[0]
    mirror = (tmp_path / "logs" / "deepfix-cli.log").read_bytes()
    assert b"OUT [redacted]" in mirror
    assert b"ERR [redacted]" in mirror
    assert SECRET.encode() not in mirror
    assert b"deepfix-heartbeat" in mirror

    local = tmp_path / "work" / "runs" / "run-77" / "output" / "heartbeat.json"
    uploaded = (
        tmp_path
        / "objects"
        / BUCKET
        / "optimization"
        / "runs"
        / "run-77"
        / "output"
        / "heartbeat.json"
    )
    body = json.loads(uploaded.read_text(encoding="utf-8"))
    assert json.loads(local.read_text(encoding="utf-8"))["written_at"] == body["written_at"]
    assert set(body) == {"run_id", "pid", "elapsed_s", "argv0", "written_at"}
    assert body["run_id"] == "run-77"
    assert body["argv0"] == "deepfix"
    assert isinstance(body["pid"], int) and body["pid"] > 0
    assert isinstance(body["elapsed_s"], int) and body["elapsed_s"] >= 0
    assert SECRET not in uploaded.read_text(encoding="utf-8")


def _run_env(tmp_path: Path, child: Path, **extra: str) -> dict[str, str]:
    """The environment a run sees, with no inherited run id unless asked for."""
    env = _env(tmp_path, child)
    env.pop("DEEPFIX_RUN_ID", None)
    env.update(extra)
    return env


def test_help_does_not_heartbeat_or_borrow_a_previous_runs_marker(tmp_path: Path) -> None:
    """HPO-007 printed the previous run id for `deepfix hpo --help`.

    `deepfix-sync download` leaves .deepfix-run-id at the work root, so a marker
    lookup from the workspace root returned the run before this one.
    """
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0")
    work = tmp_path / "work"
    runs = work / "runs"
    runs.mkdir(parents=True)
    (work / ".deepfix-run-id").write_text("0d825ba73fcb43ae8e8936607c4a6a24\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "hpo", "--help"],
        env=_run_env(tmp_path, child, DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="0.2"),
        cwd=work,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 3
    assert b"deepfix-heartbeat" not in completed.stderr
    assert b"0d825ba73fcb43ae8e8936607c4a6a24" not in completed.stderr
    # Nothing was written locally or uploaded for a run that is not active.
    assert not (work / "runs").exists() or not any((work / "runs").iterdir())
    assert not (tmp_path / "objects").exists()


def test_a_marker_gives_the_run_id_only_inside_that_run(tmp_path: Path) -> None:
    """A command that names no run still heartbeats when it is inside one."""
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0.6")
    work = tmp_path / "work"
    run_dir = work / "runs" / "run-inside"
    (run_dir / "input").mkdir(parents=True)
    (work / ".deepfix-run-id").write_text("run-inside\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "eval"],
        env=_run_env(tmp_path, child, DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="0.15"),
        cwd=run_dir / "input",
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 3
    assert b"deepfix-heartbeat run_id=run-inside" in completed.stderr
    local = run_dir / "output" / "heartbeat.json"
    assert json.loads(local.read_text(encoding="utf-8"))["run_id"] == "run-inside"


def test_a_marker_at_the_work_root_is_not_a_run_for_a_workspace_cwd(tmp_path: Path) -> None:
    """Standing in the workspace root is not standing in a run."""
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".deepfix-run-id").write_text("stale-run\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "eval"],
        env=_run_env(tmp_path, child, DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="30"),
        cwd=work,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 3
    assert b"deepfix-heartbeat" not in completed.stderr
    assert b"stale-run" not in completed.stderr


def test_the_current_run_env_wins_over_a_stale_marker(tmp_path: Path) -> None:
    """DEEPFIX_RUN_ID from the launcher is the run; a marker never overrides it."""
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0.6")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".deepfix-run-id").write_text("stale-run\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "eval"],
        env=_run_env(
            tmp_path,
            child,
            DEEPFIX_RUN_ID="current-run",
            DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="0.15",
        ),
        cwd=work,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert b"deepfix-heartbeat run_id=current-run" in completed.stderr
    assert b"stale-run" not in completed.stderr


def test_a_direct_interpreter_call_does_not_write_a_heartbeat(tmp_path: Path) -> None:
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0")
    env = _env(tmp_path, child)
    completed = subprocess.run(
        [str(child), "--help"],
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 3
    assert not (tmp_path / "objects").exists()
    assert b"deepfix-heartbeat" not in completed.stderr


def test_the_default_interval_is_thirty_seconds() -> None:
    assert HEARTBEAT_INTERVAL_SECONDS == 30


def test_redactor_catches_a_secret_split_across_chunks() -> None:
    secret = SECRET.encode()
    redactor = _Redactor((secret,))
    split = len(secret) // 2
    first = redactor.feed(b"OUT " + secret[:split])
    second = redactor.feed(secret[split:] + b"\n")
    tail = redactor.finish()
    assert first + second + tail == b"OUT [redacted]\n"
    assert secret not in first + second + tail


EVAL_ENV_BODY = (
    "LLM_MODEL=fixture/llm\n"
    "LLM_JUDGE_MODEL=fixture/judge\n"
    "EMBEDDING_MODEL=fixture/embed\n"
    "COHERE_MODEL=cohere/rerank-4-fast\n"
    "COHERE_BASE_URL=https://openrouter.ai/api/v1\n"
    "NOT_AN_EVAL_KEY=ignored-value\n"
)

OPENROUTER_KEY = "sk-or-shim-secret-key"


def _dump_child(path: Path) -> None:
    """A child that prints the environment it received as JSON on stdout."""
    path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            body = {
                "argv": sys.argv[1:],
                "env": dict(os.environ),
            }
            sys.stdout.write(json.dumps(body) + "\\n")
            sys.stdout.flush()
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _scrubbed_env(tmp_path: Path, child: Path, **extra: str) -> dict[str, str]:
    """The env the terminal leaves behind: no model ids, no OpenRouter key."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DEEPFIX_REAL_BIN": str(child),
        "DEEPFIX_WORK_ROOT": str(tmp_path / "work"),
        "DEEPFIX_BUNDLE_ROOT": str(tmp_path / "objects"),
        "DEEPFIX_CONTAINER_LOG": str(tmp_path / "logs" / "deepfix-cli.log"),
        "DEEPFIX_EVAL_ENV": str(tmp_path / "eval-env"),
        "HERMES_HOME": str(tmp_path / "data"),
        "S3_URI": f"s3://{BUCKET}/dev",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def _write_eval_env(tmp_path: Path) -> Path:
    path = tmp_path / "eval-env"
    path.write_text(EVAL_ENV_BODY, encoding="utf-8")
    path.chmod(0o644)
    return path


def _write_sealed_dotenv(tmp_path: Path) -> Path:
    home = tmp_path / "data"
    home.mkdir(parents=True, exist_ok=True)
    path = home / ".env"
    path.write_text(f"API_SERVER_KEY={'k' * 32}\nOPENROUTER_API_KEY={OPENROUTER_KEY}\n")
    path.chmod(0o600)
    return path


def test_the_shim_restores_eval_env_without_putting_secrets_on_argv(tmp_path: Path) -> None:
    child = tmp_path / "deepfix-real"
    _dump_child(child)
    _write_eval_env(tmp_path)
    _write_sealed_dotenv(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "eval", "--json"],
        env=_scrubbed_env(tmp_path, child),
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode()
    body = json.loads(completed.stdout.decode())
    child_env = body["env"]
    assert child_env["LLM_MODEL"] == "fixture/llm"
    assert child_env["LLM_JUDGE_MODEL"] == "fixture/judge"
    assert child_env["EMBEDDING_MODEL"] == "fixture/embed"
    assert child_env["COHERE_MODEL"] == "cohere/rerank-4-fast"
    assert child_env["COHERE_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert child_env["OPENROUTER_API_KEY"] == OPENROUTER_KEY
    # The OpenRouter key fills the slots the pipeline reads, and the child still
    # has no product LLM key from the Hermes environment.
    assert child_env["LLM_API_KEY"] == OPENROUTER_KEY
    assert child_env["EMBEDDING_API_KEY"] == OPENROUTER_KEY
    assert child_env["COHERE_API_KEY"] == OPENROUTER_KEY
    assert "NOT_AN_EVAL_KEY" not in child_env
    # Nothing secret on argv, and no model ids smuggled onto the command line.
    argv = " ".join(body["argv"])
    assert OPENROUTER_KEY not in argv
    assert "fixture/llm" not in argv
    # The mirrored container log still redacts the key the shim restored.
    mirror = (tmp_path / "logs" / "deepfix-cli.log").read_bytes()
    assert OPENROUTER_KEY.encode() not in mirror
    assert b"[redacted]" in mirror


def test_a_non_openrouter_base_does_not_receive_the_openrouter_key(tmp_path: Path) -> None:
    from deepfix_rag.deepfix_shim import injected_child_env

    eval_env = tmp_path / "eval-env"
    eval_env.write_text(
        "LLM_MODEL=fixture/llm\nLLM_BASE_URL=https://api.example.test/v1\n"
        "COHERE_BASE_URL=https://api.example.test/v1\n",
        encoding="utf-8",
    )
    child = injected_child_env(
        {
            "OPENROUTER_API_KEY": OPENROUTER_KEY,
            "EMBEDDING_BASE_URL": "https://openrouter.ai/api/v1",
        },
        eval_env_path=eval_env,
    )
    assert child["LLM_MODEL"] == "fixture/llm"
    assert "LLM_API_KEY" not in child
    assert "COHERE_API_KEY" not in child
    # Embedding is still on OpenRouter, so it gets the key.
    assert child["EMBEDDING_API_KEY"] == OPENROUTER_KEY


def test_an_existing_key_slot_is_not_overwritten(tmp_path: Path) -> None:
    from deepfix_rag.deepfix_shim import injected_child_env

    eval_env = tmp_path / "eval-env"
    eval_env.write_text("LLM_MODEL=fixture/llm\n", encoding="utf-8")
    child = injected_child_env(
        {"OPENROUTER_API_KEY": OPENROUTER_KEY, "LLM_API_KEY": "already-set-key"},
        eval_env_path=eval_env,
    )
    assert child["LLM_API_KEY"] == "already-set-key"


def test_the_injection_path_never_reads_the_s6_env_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent-facing workaround stays retired: no container_environment reads."""
    from deepfix_rag import deepfix_shim

    eval_env = _write_eval_env(tmp_path)
    read_paths: list[str] = []
    original_read_text = Path.read_text

    def _record(self: Path, *args, **kwargs):  # noqa: ANN001 - Path API
        read_paths.append(str(self))
        if "container_environment" in str(self):
            raise AssertionError(f"read of {self} is not allowed")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _record)

    child = deepfix_shim.injected_child_env(
        {"HERMES_HOME": str(tmp_path / "data")},
        eval_env_path=eval_env,
    )
    assert child["LLM_MODEL"] == "fixture/llm"
    assert read_paths == [str(eval_env), str(tmp_path / "data" / ".env")]
    assert not any("/run/s6" in value for value in child.values())
    assert not any("container_environment" in value for value in child.values())

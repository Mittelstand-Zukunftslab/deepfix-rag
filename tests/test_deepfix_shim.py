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
        timeout=10,
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


def test_marker_file_supplies_the_run_id_when_argv_has_none(tmp_path: Path) -> None:
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".deepfix-run-id").write_text("run-marker\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.deepfix_shim", "--help"],
        env=_env(tmp_path, child, DEEPFIX_HEARTBEAT_INTERVAL_SECONDS="30"),
        cwd=work,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 3
    uploaded = (
        tmp_path
        / "objects"
        / BUCKET
        / "optimization"
        / "runs"
        / "run-marker"
        / "output"
        / "heartbeat.json"
    )
    assert json.loads(uploaded.read_text(encoding="utf-8"))["run_id"] == "run-marker"


def test_a_direct_interpreter_call_does_not_write_a_heartbeat(tmp_path: Path) -> None:
    child = tmp_path / "deepfix-real"
    _child(child, sleep="0")
    env = _env(tmp_path, child)
    completed = subprocess.run(
        [str(child), "--help"],
        env=env,
        capture_output=True,
        timeout=10,
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

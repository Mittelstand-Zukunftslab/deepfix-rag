"""Prefix scoping against a real RustFS process.

Marked ``network`` so the default suite does not start it. Set
``DEEPFIX_RUSTFS_BIN`` to the ``rustfs`` binary, or place the official
binary at ``/tmp/rustfs-bin/rustfs``. This environment has no container
runtime, so the check runs the binary rather than ``rustfs/rustfs:latest``.
Secrets stay in process memory and are not printed.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import httpx
import pytest

from deepfix_rag.hermes_iam import HERMES_IAM_USER, provision_hermes_user
from deepfix_rag.run_bundle import S3ObjectStore

pytestmark = [pytest.mark.network]

_DEFAULT_BIN = Path("/tmp/rustfs-bin/rustfs")
_ROOT_ACCESS_KEY = "deepfix-root-key"


def _binary() -> Path | None:
    raw = os.environ.get("DEEPFIX_RUSTFS_BIN", "").strip()
    path = Path(raw) if raw else _DEFAULT_BIN
    if path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _error_code(call: Callable[[], object]) -> str:
    try:
        call()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        return str(code)
    return "ok"


def test_hermes_user_is_limited_to_the_run_prefix(tmp_path: Path) -> None:
    binary = _binary()
    if binary is None:
        pytest.skip("RustFS binary is not available")
    port = _free_port()
    root_secret = secrets.token_hex(32)
    hermes_secret = secrets.token_hex(32)
    data = tmp_path / "data"
    data.mkdir()
    endpoint = f"http://127.0.0.1:{port}"
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "RUSTFS_ACCESS_KEY": _ROOT_ACCESS_KEY,
        "RUSTFS_SECRET_KEY": root_secret,
        "RUSTFS_ADDRESS": f"127.0.0.1:{port}",
        "RUSTFS_CONSOLE_ENABLE": "false",
    }
    process = subprocess.Popen(
        [str(binary), "server", str(data)],
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until_healthy(endpoint, process)
        root = S3ObjectStore(
            endpoint_url=endpoint,
            access_key_id=_ROOT_ACCESS_KEY,
            secret_access_key=root_secret,
        )
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_ROOT_ACCESS_KEY,
            aws_secret_access_key=root_secret,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        ).create_bucket(Bucket="deepfix-index")
        access_key_id = provision_hermes_user(
            endpoint_url=endpoint,
            root_access_key=_ROOT_ACCESS_KEY,
            root_secret_key=root_secret,
            bucket="deepfix-index",
            secret_key=hermes_secret,
        )
        assert access_key_id == HERMES_IAM_USER
        assert access_key_id != _ROOT_ACCESS_KEY
        scoped = S3ObjectStore(
            endpoint_url=endpoint,
            access_key_id=access_key_id,
            secret_access_key=hermes_secret,
        )
        root.put("deepfix-index", "dev/not-in-prefix.txt", b"root-object")
        root.put("deepfix-index", "holdout/questions.jsonl", b"holdout-row")
        root.put("deepfix-index", "optimization/other.txt", b"outside-runs")
        inside = "optimization/runs/probe/output/result.json"
        scoped.put("deepfix-index", inside, b"scoped-ok")
        assert scoped.get("deepfix-index", inside) == b"scoped-ok"
        assert root.get("deepfix-index", inside) == b"scoped-ok"
        listed = scoped.list("deepfix-index", "optimization/runs/probe/")
        assert inside in listed
        scoped.delete("deepfix-index", inside)
        assert _error_code(lambda: scoped.get("deepfix-index", "dev/not-in-prefix.txt")) == (
            "AccessDenied"
        )
        assert (
            _error_code(lambda: scoped.get("deepfix-index", "holdout/questions.jsonl"))
            == "AccessDenied"
        )
        assert _error_code(
            lambda: scoped.put("deepfix-index", "holdout/questions.jsonl", b"x")
        ) == ("AccessDenied")
        assert _error_code(lambda: scoped.delete("deepfix-index", "dev/not-in-prefix.txt")) == (
            "AccessDenied"
        )
        assert _error_code(lambda: scoped.get("deepfix-index", "optimization/other.txt")) == (
            "AccessDenied"
        )
        assert _error_code(lambda: scoped.list("deepfix-index", "holdout/")) == "AccessDenied"
        assert _error_code(lambda: scoped.list("deepfix-index", "dev/")) == "AccessDenied"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _wait_until_healthy(endpoint: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("RustFS exited before it became healthy")
        try:
            response = httpx.get(f"{endpoint}/health", timeout=0.5)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.2)
    pytest.fail("RustFS did not become healthy")

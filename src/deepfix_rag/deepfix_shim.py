"""Shim that keeps a long ``deepfix`` command inside the stall window.

The Hermes image puts this module on ``PATH`` as ``deepfix``. The real CLI
stays at ``DEEPFIX_REAL_BIN`` (the virtualenv console script). A direct call
to that interpreter does not write a heartbeat.

While the child runs, this process rewrites ``output/heartbeat.json`` every
``DEEPFIX_HEARTBEAT_INTERVAL_SECONDS`` (default 30) and prints one
``deepfix-heartbeat`` line on its own stderr. The child's stdout and stderr
are forwarded unchanged. A redacted copy is appended to
``DEEPFIX_CONTAINER_LOG`` when that variable is set. The pinned image's
terminal tool pipes both streams, so s6 does not see them until a service
tails that file.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from deepfix_rag.hermes_sync import bucket_from_env, store_from_env
from deepfix_rag.run_bundle import (
    CONTAINER_WORK_ROOT,
    RunBundleError,
    container_work_dir,
    run_prefix,
    validate_run_id,
)

HEARTBEAT_INTERVAL_SECONDS = 30.0
_MIN_INTERVAL_SECONDS = 0.05
_HEARTBEAT_NAME = "output/heartbeat.json"
_MARKER_NAME = ".deepfix-run-id"
_ARGV0 = "deepfix"
_SECRET_ENVS = (
    "API_SERVER_KEY",
    "OPENROUTER_API_KEY",
    "S3_SECRET_ACCESS_KEY",
    "HERMES_S3_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
)
# Model ids and endpoint URLs the terminal scrub removes from a command. The
# sandbox writes them at boot to a root-owned file; this shim puts them back
# into the CLI child's environment. They are not secrets and never go on argv.
EVAL_ENV_KEYS: tuple[str, ...] = (
    "LLM_MODEL",
    "LLM_JUDGE_MODEL",
    "LLM_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_BASE_URL",
    "COHERE_MODEL",
    "COHERE_BASE_URL",
)
DEFAULT_EVAL_ENV_PATH = Path("/opt/deepfix/eval-env")
DEFAULT_HERMES_HOME = Path("/opt/data")
# The OpenRouter key is mapped into these slots only when the matching base URL
# is OpenRouter's, so a non-OpenRouter endpoint never receives that key.
_OPENROUTER_KEY_SLOTS: tuple[tuple[str, str], ...] = (
    ("LLM_API_KEY", "LLM_BASE_URL"),
    ("EMBEDDING_API_KEY", "EMBEDDING_BASE_URL"),
    ("COHERE_API_KEY", "COHERE_BASE_URL"),
)
_DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_EMBEDDING_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_COHERE_BASE_URL = "https://openrouter.ai/api/v1"
_MIN_SECRET_LENGTH = 8
_RUN_IN_PATH = re.compile(r"(?:^|[\\/])runs[\\/]([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?=[\\/]|$)")
_RUN_IN_KEY = re.compile(r"(?:^|/)optimization/runs/([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?=/|$)")
_IO_LOCK = threading.Lock()


class _Redactor:
    """Replace secrets in a byte stream, including a secret split across reads."""

    def __init__(self, secrets: tuple[bytes, ...]) -> None:
        self._secrets = tuple(secret for secret in secrets if secret)
        self._pending = b""

    def feed(self, data: bytes) -> bytes:
        buf = self._pending + data
        hold = _hold_back(buf, self._secrets)
        if hold:
            emit, self._pending = buf[:-hold], buf[-hold:]
        else:
            emit, self._pending = buf, b""
        return _replace(emit, self._secrets)

    def finish(self) -> bytes:
        emit = self._pending
        self._pending = b""
        return _replace(emit, self._secrets)


def main(argv: list[str] | None = None) -> int:
    """Run the real CLI and heartbeat until it exits."""
    args = list(sys.argv[1:] if argv is None else argv)
    real = os.environ.get("DEEPFIX_REAL_BIN", "/opt/deepfix/.venv/bin/deepfix")
    if not Path(real).is_file():
        _write_stderr(b"deepfix: real CLI is missing\n")
        return 127
    interval = _interval(os.environ)
    run_id = resolve_run_id(args, os.environ, Path.cwd())
    child_env = injected_child_env(os.environ)
    # The restored child env is the source of redaction too, so a key that came
    # from the sealed profile ``.env`` is still scrubbed from the log.
    secrets = _secret_bytes(child_env)
    mirror = _open_mirror()
    stdout_redactor = _Redactor(secrets)
    stderr_redactor = _Redactor(secrets)
    try:
        child = subprocess.Popen(
            [real, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )
    except OSError:
        _write_stderr(b"deepfix: real CLI could not be started\n")
        _close_mirror(mirror)
        return 127
    assert child.stdout is not None
    assert child.stderr is not None
    started = time.monotonic()
    stdout_thread = threading.Thread(
        target=_pump,
        args=(child.stdout, sys.stdout.buffer, stdout_redactor, mirror, secrets),
        name="deepfix-shim-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_pump,
        args=(child.stderr, sys.stderr.buffer, stderr_redactor, mirror, secrets),
        name="deepfix-shim-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    _emit_heartbeat(run_id, child.pid, 0, mirror, secrets)
    stop = threading.Event()

    def _later() -> None:
        while not stop.wait(interval):
            if child.poll() is not None:
                return
            elapsed = int(time.monotonic() - started)
            _emit_heartbeat(run_id, child.pid, elapsed, mirror, secrets)

    later = threading.Thread(target=_later, name="deepfix-shim-heartbeat", daemon=True)
    later.start()
    return_code = child.wait()
    stop.set()
    later.join(timeout=2)
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    _write_mirror(mirror, stdout_redactor.finish(), secrets)
    _write_mirror(mirror, stderr_redactor.finish(), secrets)
    _close_mirror(mirror)
    return return_code


def resolve_run_id(argv: list[str], env: Mapping[str, str], cwd: Path) -> str | None:
    """The DeepFix run id this command belongs to, or None."""
    explicit = env.get("DEEPFIX_RUN_ID", "").strip()
    if _is_run_id(explicit):
        return explicit
    if "--run-id" in argv:
        index = argv.index("--run-id")
        if index + 1 < len(argv) and _is_run_id(argv[index + 1]):
            return argv[index + 1]
    joined = " ".join(argv)
    for pattern in (_RUN_IN_PATH, _RUN_IN_KEY):
        match = pattern.search(joined)
        if match and _is_run_id(match.group(1)):
            return match.group(1)
    marked = _marker_run_id(cwd)
    if marked:
        return marked
    raw_root = env.get("DEEPFIX_WORK_ROOT", "").strip()
    if raw_root:
        return _marker_run_id(Path(raw_root))
    return _marker_run_id(CONTAINER_WORK_ROOT)


def publish_heartbeat(
    *,
    run_id: str,
    pid: int,
    elapsed_s: int,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Write ``output/heartbeat.json`` locally and, when configured, to the run prefix.

    Store failures do not raise. The CLI must keep running when the upload fails.
    """
    source = os.environ if env is None else env
    written_at = datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {
            "run_id": run_id,
            "pid": pid,
            "elapsed_s": elapsed_s,
            "argv0": _ARGV0,
            "written_at": written_at,
        },
        separators=(",", ":"),
    ).encode()
    root = _work_root(source)
    try:
        target = container_work_dir(run_id, root) / _HEARTBEAT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError:
        _write_stderr(b"deepfix-heartbeat local write failed\n")
    try:
        store = store_from_env(source)
        bucket = bucket_from_env(source)
        store.put(bucket, f"{run_prefix(run_id)}/{_HEARTBEAT_NAME}", body)
    except RunBundleError:
        return body
    except Exception:
        _write_stderr(b"deepfix-heartbeat upload failed\n")
    return body


def _emit_heartbeat(
    run_id: str | None,
    pid: int,
    elapsed_s: int,
    mirror: object,
    secrets: tuple[bytes, ...],
) -> None:
    if run_id:
        publish_heartbeat(run_id=run_id, pid=pid, elapsed_s=elapsed_s)
    line = (
        f"deepfix-heartbeat run_id={run_id or ''} pid={pid} elapsed_s={elapsed_s} argv0={_ARGV0}\n"
    ).encode()
    _write_stderr(line)
    _write_mirror(mirror, line, secrets)


def _pump(
    source: object,
    dest: object,
    redactor: _Redactor,
    mirror: object,
    secrets: tuple[bytes, ...],
) -> None:
    reader = getattr(source, "read", None)
    writer = getattr(dest, "write", None)
    if reader is None or writer is None:
        return
    while True:
        chunk = reader(65536)
        if not chunk:
            break
        with _IO_LOCK:
            writer(chunk)
            flush = getattr(dest, "flush", None)
            if flush is not None:
                flush()
        _write_mirror(mirror, redactor.feed(chunk), secrets)


def _interval(env: Mapping[str, str]) -> float:
    raw = env.get("DEEPFIX_HEARTBEAT_INTERVAL_SECONDS", "").strip()
    if not raw:
        return HEARTBEAT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return HEARTBEAT_INTERVAL_SECONDS
    if value < _MIN_INTERVAL_SECONDS:
        return _MIN_INTERVAL_SECONDS
    return value


def read_eval_env(path: Path | None = None) -> dict[str, str]:
    """The non-secret eval settings the sandbox wrote at boot. Missing file is empty."""
    target = path or _eval_env_path(os.environ)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in EVAL_ENV_KEYS:
            continue
        value = value.strip()
        if value:
            values[key] = value
    return values


def _eval_env_path(env: Mapping[str, str]) -> Path:
    raw = env.get("DEEPFIX_EVAL_ENV", "").strip()
    return Path(raw) if raw else DEFAULT_EVAL_ENV_PATH


def _is_openrouter_base(base_url: str | None) -> bool:
    text = (base_url or "").strip().rstrip("/").lower()
    if not text:
        return True
    parts = urlsplit(text)
    return parts.netloc == "openrouter.ai"


def injected_child_env(
    env: Mapping[str, str] | None = None,
    eval_env_path: Path | None = None,
) -> dict[str, str]:
    """The CLI child's environment: model ids restored, OpenRouter key mapped.

    The terminal scrub removes model ids and the OpenRouter key from a
    command. The sandbox writes the model ids to a root-owned file at boot
    and keeps the key in the sealed profile ``.env``. This restores both
    without putting anything on the command line, and without reading s6's
    ``container_environment`` directories.
    """
    source = dict(os.environ if env is None else env)
    child = dict(source)
    settings_values = read_eval_env(eval_env_path)
    child.update(settings_values)

    key = _openrouter_key(source)
    if key:
        child["OPENROUTER_API_KEY"] = key
        for slot, base_env in _OPENROUTER_KEY_SLOTS:
            if (child.get(slot) or "").strip():
                continue
            if not _is_openrouter_base(child.get(base_env, source.get(base_env))):
                continue
            child[slot] = key
    return child


def _openrouter_key(env: Mapping[str, str]) -> str | None:
    """The OpenRouter key from the environment or the sealed profile ``.env``."""
    direct = (env.get("OPENROUTER_API_KEY") or "").strip()
    if direct:
        return direct
    if env.get("DEEPFIX_READ_PROFILE_ENV", "").strip() == "0":
        return None
    return _profile_env_value("OPENROUTER_API_KEY", env)


def _profile_env_value(name: str, env: Mapping[str, str]) -> str | None:
    home = Path(env.get("HERMES_HOME", "").strip() or DEFAULT_HERMES_HOME)
    path = home / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        if not stripped.startswith(f"{name}="):
            continue
        value = stripped.split("=", 1)[1].strip().strip("'\"")
        return value or None
    return None


def _is_run_id(value: str) -> bool:
    try:
        validate_run_id(value)
    except RunBundleError:
        return False
    return True


def _marker_run_id(start: Path) -> str | None:
    current = start
    for _ in range(8):
        path = current / _MARKER_NAME
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if _is_run_id(text):
                    return text
        except OSError:
            return None
        if current.parent == current:
            return None
        current = current.parent
    return None


def _work_root(env: Mapping[str, str]) -> Path:
    raw = env.get("DEEPFIX_WORK_ROOT", "").strip()
    if raw:
        return Path(raw)
    return CONTAINER_WORK_ROOT


def _secret_bytes(env: Mapping[str, str], extra: tuple[str, ...] = ()) -> tuple[bytes, ...]:
    found: list[bytes] = []
    for name in (*_SECRET_ENVS, *extra):
        value = env.get(name, "").strip()
        if len(value) < _MIN_SECRET_LENGTH:
            continue
        encoded = value.encode("utf-8")
        if encoded not in found:
            found.append(encoded)
    found.sort(key=len, reverse=True)
    return tuple(found)


def _open_mirror() -> object:
    raw = os.environ.get("DEEPFIX_CONTAINER_LOG", "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab")
    except OSError:
        _write_stderr(b"deepfix-heartbeat mirror unavailable\n")
        return None


def _write_mirror(mirror: object, data: bytes, secrets: tuple[bytes, ...]) -> None:
    if mirror is None or not data:
        return
    writer = getattr(mirror, "write", None)
    if writer is None:
        return
    # ``data`` for the child's streams is already redacted. Heartbeat lines
    # are not. Replacing again is a no-op when the secrets are already gone.
    cleaned = _replace(data, secrets)
    with _IO_LOCK:
        try:
            writer(cleaned)
            flush = getattr(mirror, "flush", None)
            if flush is not None:
                flush()
        except OSError:
            return


def _close_mirror(mirror: object) -> None:
    close = getattr(mirror, "close", None)
    if close is None:
        return
    try:
        close()
    except OSError:
        return


def _write_stderr(data: bytes) -> None:
    with _IO_LOCK:
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()


def _hold_back(buf: bytes, secrets: tuple[bytes, ...]) -> int:
    if not secrets or not buf:
        return 0
    longest = max(len(secret) for secret in secrets) - 1
    hold = min(longest, len(buf))
    while hold > 0 and not any(secret.startswith(buf[-hold:]) for secret in secrets):
        hold -= 1
    return hold


def _replace(data: bytes, secrets: tuple[bytes, ...]) -> bytes:
    for secret in secrets:
        data = data.replace(secret, b"[redacted]")
    return data


if __name__ == "__main__":
    raise SystemExit(main())

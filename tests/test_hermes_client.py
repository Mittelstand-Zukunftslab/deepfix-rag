"""Hermes runs client against a local HTTP server. No pipeline mocks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import socket
import threading
import time

import pytest

from deepfix_rag.config.settings import ModelNotConfigured, settings
from deepfix_rag.hermes_client import (
    HERMES_EVENT_NAMES,
    HERMES_MAX_TURNS,
    HERMES_PROVIDER,
    HERMES_RUN_STATUSES,
    HermesClient,
    HermesClientError,
    HermesConflict,
    HermesStreamError,
    optimization_instructions,
)

pytestmark = [pytest.mark.unit]

API_KEY = "test-api-server-key-0123456789abcdef"
DEEPFIX_RUN = "runa0001"
HERMES_RUN = "run_abc123"
REPO = Path(__file__).resolve().parents[1]
Script = Callable[[BaseHTTPRequestHandler, "HermesApi"], None]


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    authorized: bool
    authorization_present: bool = False


class _ReuseHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class HermesApi:
    """Tiny Hermes runs API. Scripts, when present, answer before the default."""

    def __init__(self, api_key: str = API_KEY) -> None:
        self.api_key = api_key
        self.calls: list[Call] = []
        self.runs: dict[str, dict[str, object]] = {}
        self.released = threading.Event()
        self._idem: dict[str, tuple[bytes, str]] = {}
        self._scripts: list[Script] = []
        self._counter = 0
        self._lock = threading.Lock()
        self.health_ok = True
        self.on_accept: Callable[[str, str], None] | None = None
        self.events_body: bytes | None = None
        self._host = "127.0.0.1"
        self._port: int | None = None
        self._bind()

    def __enter__(self) -> HermesApi:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.released.set()
        if self._thread.is_alive():
            self._httpd.shutdown()
            self._httpd.server_close()
            self._thread.join(timeout=5)

    def suspend(self) -> None:
        """Stop listening so new connections are refused. Stored runs stay put."""
        if self._thread.is_alive():
            self._httpd.shutdown()
            self._httpd.server_close()
            self._thread.join(timeout=5)

    def resume(self) -> None:
        """Listen again on the same port after ``suspend``."""
        self._bind()
        self._thread.start()

    def _bind(self) -> None:
        port = 0 if self._port is None else self._port
        self._httpd = _ReuseHTTPServer((self._host, port), _Handler)
        self._httpd.api = self  # type: ignore[attr-defined]
        if self._port is None:
            self._port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def script(self, fn: Script) -> None:
        with self._lock:
            self._scripts.append(fn)

    def pop_script(self) -> Script | None:
        with self._lock:
            if not self._scripts:
                return None
            return self._scripts.pop(0)

    def record(self, call: Call) -> None:
        with self._lock:
            self.calls.append(call)

    def default(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        path = handler.path.split("?", 1)[0]
        method = handler.command
        if method == "POST" and path == "/v1/runs":
            self._start(handler, body)
            return
        run_id, action = _split_run_path(path)
        if run_id is None:
            _send_json(handler, 404, _error("not_found", "not found"))
            return
        if method == "GET" and action == "events":
            payload = self.events_body
            if payload is None:
                payload = _stream(tool_started=True, keepalive=True, terminal="run.completed")
            _send_sse(handler, payload)
            return
        if method == "POST" and action == "stop":
            run = self.runs.get(run_id)
            if run is not None and run.get("status") not in {"completed", "failed", "cancelled"}:
                run["status"] = "stopping"
            _send_json(handler, 200, {"status": "stopping", "run_id": run_id})
            return
        if method == "POST" and action == "resolve-unknown":
            self._resolve(handler, run_id, body)
            return
        if method == "GET" and action is None:
            run = self.runs.get(run_id)
            if run is None:
                _send_json(handler, 404, _error("run_not_found", f"Run not found: {run_id}"))
                return
            _send_json(handler, 200, run)
            return
        _send_json(handler, 404, _error("not_found", "not found"))

    def _start(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        key = handler.headers.get("Idempotency-Key", "")
        if key and key in self._idem:
            previous, hermes_id = self._idem[key]
            if previous != body:
                _send_json(
                    handler,
                    409,
                    _error("idempotency_key_conflict", "different payload"),
                )
                return
            _send_json(
                handler,
                202,
                {"run_id": hermes_id, "status": "started", "replayed": True},
                extra={"Idempotency-Replayed": "true"},
            )
            return
        self._counter += 1
        hermes_id = f"run_{self._counter:032x}"
        if key:
            self._idem[key] = (body, hermes_id)
        self.runs[hermes_id] = {
            "object": "hermes.run",
            "run_id": hermes_id,
            "status": "started",
        }
        if self.on_accept is not None:
            self.on_accept(hermes_id, key)
        _send_json(
            handler,
            202,
            {"run_id": hermes_id, "status": "started", "replayed": False},
        )

    def _resolve(self, handler: BaseHTTPRequestHandler, run_id: str, body: bytes) -> None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict) or set(payload) != {
            "admission_id",
            "execution_generation",
        }:
            _send_json(handler, 400, _error("invalid_params", "invalid_params"))
            return
        run = self.runs.get(run_id)
        if run is None or payload["admission_id"] != run.get("admission_id"):
            _send_json(handler, 409, _error("not_found", "not_found"))
            return
        if payload["execution_generation"] != run.get("execution_generation"):
            _send_json(handler, 409, _error("stale_generation", "stale_generation"))
            return
        _send_json(handler, 200, {"run_id": run_id, "outcome": "interrupted"})


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ThreadingHTTPServer

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        api: HermesApi = self.server.api  # type: ignore[attr-defined]
        raw = {key: value for key, value in self.headers.items()}
        authorized = raw.get("Authorization") == f"Bearer {api.api_key}"
        present = any(key.lower() == "authorization" for key in raw)
        call = Call(
            method=self.command,
            path=self.path,
            headers={key: value for key, value in raw.items() if key.lower() != "authorization"},
            body=body,
            authorized=authorized,
            authorization_present=present,
        )
        path = self.path.split("?", 1)[0]
        if self.command == "GET" and path == "/health":
            api.record(call)
            if api.health_ok:
                _send_json(
                    self,
                    200,
                    {"status": "ok", "platform": "hermes-agent", "version": "0.22.7"},
                )
            else:
                _send_json(self, 503, {"status": "unavailable"})
            return
        api.record(call)
        if not authorized:
            _send_json(self, 401, _error("unauthorized", "unauthorized"))
            return
        script = api.pop_script()
        if script is not None:
            script(self, api)
            return
        api.default(self, body)


def _error(code: str, message: str) -> dict[str, object]:
    return {"error": {"message": message, "type": "invalid_request_error", "code": code}}


def _send_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, object],
    *,
    extra: dict[str, str] | None = None,
) -> None:
    raw = json.dumps(payload).encode()
    handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(raw)


def _send_sse(handler: BaseHTTPRequestHandler, payload: bytes) -> None:
    handler.close_connection = True
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(payload)


def _frame(seq: int, name: str, **fields: object) -> bytes:
    payload = {"event": name, "run_id": HERMES_RUN, "seq": seq, **fields}
    return f"id: {seq}\ndata: {json.dumps(payload)}\n\n".encode()


def _stream(*, tool_started: bool, keepalive: bool, terminal: str) -> bytes:
    parts: list[bytes] = []
    seq = 1
    if tool_started:
        parts.append(_frame(seq, "tool.started", tool="terminal", preview="deepfix eval"))
        seq += 1
    if keepalive:
        parts.append(b": keepalive\n\n")
    parts.append(_frame(seq, terminal, output="done"))
    return b"".join(parts)


def _split_run_path(path: str) -> tuple[str | None, str | None]:
    prefix = "/v1/runs/"
    if not path.startswith(prefix):
        return None, None
    rest = path[len(prefix) :]
    for action in ("events", "stop", "resolve-unknown"):
        suffix = f"/{action}"
        if rest.endswith(suffix):
            return rest[: -len(suffix)], action
    if "/" in rest:
        return None, None
    return rest, None


def _client(api: HermesApi, **kwargs: object) -> HermesClient:
    return HermesClient(api.base_url, api.api_key, **kwargs)  # type: ignore[arg-type]


def _assert_secret_absent(text: str) -> None:
    assert API_KEY not in text
    assert "Bearer " not in text


def test_known_statuses_and_events_are_distinct() -> None:
    assert HERMES_RUN_STATUSES == {
        "started",
        "running",
        "stopping",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "unknown",
    }
    assert {
        "tool.started",
        "tool.completed",
        "tool.failed",
        "message.delta",
        "message.interim",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.interrupted",
        "keepalive",
    } <= HERMES_EVENT_NAMES


def test_empty_key_and_credential_url_are_rejected() -> None:
    with pytest.raises(HermesClientError) as empty:
        HermesClient("http://hermes:8642", " ")
    _assert_secret_absent(str(empty.value))
    assert empty.value.marks_run_failed is False

    with pytest.raises(HermesClientError) as leaked:
        HermesClient(f"http://hermes:8642/?access_token={API_KEY}", API_KEY)
    _assert_secret_absent(str(leaked.value))
    assert leaked.value.__cause__ is None
    assert leaked.value.__context__ is None


def test_timeouts_are_explicit_and_a_silent_server_cannot_hang() -> None:
    with HermesApi() as api:
        with _client(api, connect_timeout=0.4, read_timeout=0.4) as client:
            assert client._json_timeout.connect == 0.4
            assert client._json_timeout.read == 0.4
            assert client._json_timeout.write is not None
            assert client._json_timeout.pool is not None
            assert client._sse_timeout.connect == 0.4
            assert client._sse_timeout.read == client.sse_idle_timeout
            assert client._http.timeout is not None
            assert client._http.timeout.connect == 0.4
            assert client._http.timeout.read == 0.4

            def hang(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
                server.released.wait(timeout=8)

            api.script(hang)
            started = time.monotonic()
            with pytest.raises(HermesClientError) as caught:
                client.start_run(DEEPFIX_RUN)
            elapsed = time.monotonic() - started
    assert elapsed < 2
    assert caught.value.code == "timeout"
    assert caught.value.marks_run_failed is False
    _assert_secret_absent(str(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_reset_connection_is_retried_once() -> None:
    def reset(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        handler.close_connection = True
        try:
            handler.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            return

    with HermesApi() as api:
        api.script(reset)
        with _client(api) as client:
            accepted = client.start_run(DEEPFIX_RUN)
    assert accepted.hermes_run_id.startswith("run_")
    assert accepted.replayed is False
    assert len(api.calls) == 2
    assert all(call.authorized for call in api.calls)


def test_connect_timeout_is_bounded() -> None:
    started = time.monotonic()
    with HermesClient(
        "http://192.0.2.1:8642",
        API_KEY,
        connect_timeout=0.5,
        read_timeout=0.5,
    ) as client:
        with pytest.raises(HermesClientError) as caught:
            client.start_run(DEEPFIX_RUN)
    elapsed = time.monotonic() - started
    assert elapsed < 3
    assert caught.value.code in {"timeout", "transport"}
    assert caught.value.marks_run_failed is False
    _assert_secret_absent(str(caught.value))
    assert caught.value.__context__ is None


def test_start_run_sends_the_fixed_instructions_and_replays_the_original_id() -> None:
    with HermesApi() as api:
        with _client(api) as client:
            first = client.start_run(DEEPFIX_RUN)
            second = client.start_run(DEEPFIX_RUN)
    assert first.replayed is False
    assert second.replayed is True
    assert second.hermes_run_id == first.hermes_run_id
    assert second.status == "started"
    assert len(api.calls) == 2
    bodies = [json.loads(call.body) for call in api.calls]
    assert bodies[0] == bodies[1]
    body = bodies[0]
    assert set(body) == {"input", "instructions", "provider", "model"}
    assert body["provider"] == HERMES_PROVIDER
    assert body["model"] == settings.HERMES_MODEL
    assert "openrouter/" not in body["model"]
    instructions = optimization_instructions(DEEPFIX_RUN)
    assert body["input"] == instructions
    assert body["instructions"] == instructions
    assert "holdout" not in instructions.lower()
    assert "product.db" not in instructions
    assert "feedback_dev.jsonl" in instructions
    assert "champion.yaml" in instructions
    assert f"/opt/data/workspace/runs/{DEEPFIX_RUN}" in instructions
    assert "deepfix-sync download" in instructions
    assert "deepfix-sync upload" in instructions
    assert "optimization-loop" in instructions
    assert "dense, hybrid, and bm25" in instructions
    assert f"{HERMES_MAX_TURNS} agent turns" in instructions
    assert "not a cancel" in instructions
    assert "deepfix hpo" in instructions
    assert "hpo-search" in instructions
    assert "configs/hpo_retrieval_small.yaml" in instructions
    assert "ONCE" in instructions
    assert "Do not run `deepfix hpo`" not in instructions
    assert "run-diagnostic" in instructions
    for call in api.calls:
        assert call.authorized
        assert call.method == "POST"
        assert call.path == "/v1/runs"
        assert call.headers.get("Idempotency-Key") == DEEPFIX_RUN
        assert API_KEY.encode() not in call.body
        assert all(API_KEY not in value for value in call.headers.values())
        _assert_secret_absent(call.path)


def test_the_posted_model_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """HERMES_MODEL is what the API submits, and the provider can be overridden."""
    monkeypatch.setenv("HERMES_MODEL", "deepseek/fixture-optimizer")
    monkeypatch.setenv("HERMES_MODEL_PROVIDER", "vllm")
    with HermesApi() as api:
        with _client(api) as client:
            client.start_run(DEEPFIX_RUN)
    body = json.loads(api.calls[0].body)
    assert body["model"] == "deepseek/fixture-optimizer"
    assert body["provider"] == "vllm"


def test_an_explicit_model_argument_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_MODEL", "deepseek/fixture-optimizer")
    with HermesApi() as api:
        with _client(api, model="qwen/fixture-explicit") as client:
            client.start_run(DEEPFIX_RUN)
    assert json.loads(api.calls[0].body)["model"] == "qwen/fixture-explicit"


def test_a_missing_hermes_model_is_named_before_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No hardcoded fallback: the caller fails with the variable name."""
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    previous = settings.HERMES_MODEL
    settings.HERMES_MODEL = ""
    try:
        with HermesApi() as api:
            with _client(api) as client:
                with pytest.raises(ModelNotConfigured, match="HERMES_MODEL"):
                    client.start_run(DEEPFIX_RUN)
        assert api.calls == []
    finally:
        settings.HERMES_MODEL = previous


@pytest.mark.parametrize("code", ["admission_conflict", "idempotency_key_conflict"])
def test_conflicts_are_not_retried(code: str) -> None:
    def conflict(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_json(handler, 409, _error(code, code))

    with HermesApi() as api:
        api.script(conflict)
        with _client(api) as client:
            with pytest.raises(HermesConflict) as caught:
                client.start_run(DEEPFIX_RUN)
    assert len(api.calls) == 1
    assert caught.value.status_code == 409
    assert caught.value.code == code
    assert caught.value.marks_run_failed is False
    _assert_secret_absent(str(caught.value))
    assert caught.value.__context__ is None


def test_service_unavailable_honors_retry_after() -> None:
    def unavailable(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_json(
            handler,
            503,
            _error("runtime_draining", "runtime_draining"),
            extra={"Retry-After": "1"},
        )

    with HermesApi() as api:
        api.script(unavailable)
        with _client(api) as client:
            started = time.monotonic()
            accepted = client.start_run(DEEPFIX_RUN)
            elapsed = time.monotonic() - started
    assert elapsed >= 0.9
    assert elapsed < 5
    assert accepted.replayed is False
    assert accepted.hermes_run_id.startswith("run_")
    assert [call.path for call in api.calls] == ["/v1/runs", "/v1/runs"]
    assert all(call.authorized for call in api.calls)


@pytest.mark.parametrize(
    ("status", "output", "error"),
    [
        ("started", None, None),
        ("running", None, None),
        ("stopping", None, None),
        ("completed", "Done.", None),
        ("failed", None, "agent run failed"),
        ("cancelled", None, None),
        ("interrupted", None, "Gateway shutdown interrupted the run."),
        ("queued", None, None),
    ],
)
def test_get_run_returns_each_status_output_and_error(
    status: str,
    output: str | None,
    error: str | None,
) -> None:
    with HermesApi() as api:
        payload: dict[str, object] = {
            "object": "hermes.run",
            "run_id": HERMES_RUN,
            "status": status,
        }
        if output is not None:
            payload["output"] = output
        if error is not None:
            payload["error"] = error
        api.runs[HERMES_RUN] = payload
        with _client(api) as client:
            run = client.get_run(HERMES_RUN)
    assert run.status == status
    assert run.output == output
    assert run.error == error
    assert run.run_id == HERMES_RUN
    assert api.calls[0].method == "GET"
    assert api.calls[0].path == f"/v1/runs/{HERMES_RUN}"


def test_unknown_run_is_resolved_and_treated_as_interrupted() -> None:
    with HermesApi() as api:
        api.runs[HERMES_RUN] = {
            "object": "hermes.run",
            "run_id": HERMES_RUN,
            "status": "unknown",
            "admission_id": "adm_abc123",
            "execution_generation": 7,
            "error": "Gateway shutdown interrupted the run.",
        }
        with _client(api) as client:
            run = client.get_run(HERMES_RUN)
    assert run.status == "interrupted"
    assert run.admission_id == "adm_abc123"
    assert run.execution_generation == 7
    assert run.error == "Gateway shutdown interrupted the run."
    assert [call.method for call in api.calls] == ["GET", "POST"]
    assert api.calls[1].path == f"/v1/runs/{HERMES_RUN}/resolve-unknown"
    assert json.loads(api.calls[1].body) == {
        "admission_id": "adm_abc123",
        "execution_generation": 7,
    }
    assert API_KEY.encode() not in api.calls[1].body


def test_unknown_without_admission_fields_does_not_post() -> None:
    with HermesApi() as api:
        api.runs[HERMES_RUN] = {"run_id": HERMES_RUN, "status": "unknown"}
        with _client(api) as client:
            with pytest.raises(HermesClientError) as caught:
                client.get_run(HERMES_RUN)
    assert caught.value.code == "unknown_unresolved"
    assert [call.method for call in api.calls] == ["GET"]
    assert caught.value.marks_run_failed is False


def test_stop_run_reads_the_stopping_status() -> None:
    def exact(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_json(handler, 200, {"status": "stopping"})

    with HermesApi() as api:
        api.script(exact)
        with _client(api) as client:
            exact_status = client.stop_run(HERMES_RUN)
            richer_status = client.stop_run(HERMES_RUN)
    assert exact_status == "stopping"
    assert richer_status == "stopping"
    assert api.calls[0].body == b""
    assert api.calls[0].path == f"/v1/runs/{HERMES_RUN}/stop"
    assert api.calls[1].path == f"/v1/runs/{HERMES_RUN}/stop"


def test_events_yield_tool_started_keepalive_and_run_completed() -> None:
    with HermesApi() as api:
        with _client(api) as client:
            events = list(client.iter_events(HERMES_RUN))
    assert [event.name for event in events] == ["tool.started", "keepalive", "run.completed"]
    assert events[0].payload["tool"] == "terminal"
    assert events[0].seq == 1
    assert events[1].payload == {}
    assert events[1].seq is None
    assert events[2].name == "run.completed"
    assert api.calls[0].path == f"/v1/runs/{HERMES_RUN}/events"
    assert api.calls[0].headers.get("Accept") == "text/event-stream"
    assert "Last-Event-ID" not in api.calls[0].headers


def test_events_yield_the_other_lifecycle_names() -> None:
    def other(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        parts = [
            _frame(1, "tool.completed", tool="terminal", error=False, preview="ok"),
            _frame(2, "tool.failed", tool="terminal", preview="failed"),
            _frame(3, "message.delta", text="partial"),
            _frame(4, "message.interim", text="note", already_streamed=False),
            _frame(5, "run.failed", error="agent run failed"),
        ]
        _send_sse(handler, b"".join(parts))

    with HermesApi() as api:
        api.script(other)
        with _client(api) as client:
            names = [event.name for event in client.iter_events(HERMES_RUN)]
    assert names == [
        "tool.completed",
        "tool.failed",
        "message.delta",
        "message.interim",
        "run.failed",
    ]


@pytest.mark.parametrize("terminal", ["run.cancelled", "run.interrupted"])
def test_terminal_events_end_the_stream(terminal: str) -> None:
    def one(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(handler, _frame(1, terminal))

    with HermesApi() as api:
        api.script(one)
        with _client(api) as client:
            events = list(client.iter_events(HERMES_RUN))
    assert [event.name for event in events] == [terminal]


def test_dropped_stream_is_retried_and_does_not_fail_the_run() -> None:
    def drop(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(handler, _frame(1, "tool.started", tool="terminal", preview="deepfix eval"))

    def rest(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(
            handler,
            b": keepalive\n\n" + _frame(2, "run.completed", output="done"),
        )

    with HermesApi() as api:
        api.script(drop)
        api.script(rest)
        with _client(api, stream_retries=1, sse_idle_timeout=0.5) as client:
            events = list(client.iter_events(HERMES_RUN))
    assert [event.name for event in events] == ["tool.started", "keepalive", "run.completed"]
    assert len(api.calls) == 2
    assert api.calls[1].headers.get("Last-Event-ID") == "1"


def test_missing_keepalive_stalls_and_retries() -> None:
    def stall(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        first = _frame(1, "tool.started", tool="terminal", preview="deepfix eval")
        handler.close_connection = True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", "1000000")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(first)
        handler.wfile.flush()
        server.released.wait(timeout=5)

    def rest(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(handler, b": keepalive\n\n" + _frame(2, "run.completed", output="done"))

    with HermesApi() as api:
        api.script(stall)
        api.script(rest)
        with _client(api, sse_idle_timeout=0.4, stream_retries=1) as client:
            started = time.monotonic()
            events = list(client.iter_events(HERMES_RUN))
            elapsed = time.monotonic() - started
    assert elapsed < 3
    assert [event.name for event in events] == ["tool.started", "keepalive", "run.completed"]
    assert api.calls[1].headers.get("Last-Event-ID") == "1"


def test_exhausted_stream_retries_do_not_mark_the_run_failed() -> None:
    def drop(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(handler, _frame(1, "tool.started", tool="terminal", preview="deepfix eval"))

    with HermesApi() as api:
        api.script(drop)
        api.script(drop)
        with _client(api, stream_retries=1, sse_idle_timeout=0.4) as client:
            seen: list[str] = []
            with pytest.raises(HermesStreamError) as caught:
                for event in client.iter_events(HERMES_RUN):
                    seen.append(event.name)
    assert seen == ["tool.started"]
    assert caught.value.code == "stream_dropped"
    assert caught.value.marks_run_failed is False
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    _assert_secret_absent(str(caught.value))


def test_bearer_token_stays_out_of_logs_prompt_and_errors() -> None:
    def echo(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_json(handler, 500, _error("server_error", server.api_key))

    root = logging.getLogger()
    previous = root.level
    grab = _Grab()
    root.setLevel(logging.DEBUG)
    root.addHandler(grab)
    try:
        with HermesApi() as api:
            api.runs[HERMES_RUN] = {
                "run_id": HERMES_RUN,
                "status": "failed",
                "error": api.api_key,
                "output": f"prefix {api.api_key} suffix",
                "turn_exit_reason": f"max_iterations_reached({api.api_key}/40)",
            }
            api.script(echo)
            with _client(api) as client:
                with pytest.raises(HermesClientError) as caught:
                    client.start_run(DEEPFIX_RUN)
                run = client.get_run(HERMES_RUN)
    finally:
        root.removeHandler(grab)
        root.setLevel(previous)

    _assert_secret_absent(str(caught.value))
    assert "[redacted]" in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert run.error == "[redacted]"
    assert run.output == "prefix [redacted] suffix"
    assert run.turn_exit_reason == "max_iterations_reached([redacted]/40)"
    assert API_KEY not in (run.output or "")
    assert API_KEY not in (run.error or "")
    assert API_KEY not in (run.turn_exit_reason or "")
    for message in grab.messages:
        _assert_secret_absent(message)
    for call in api.calls:
        assert call.authorized
        assert API_KEY.encode() not in call.body
        assert all(API_KEY not in value for value in call.headers.values())


def test_event_preview_redacts_the_bearer_token() -> None:
    def leaked(handler: BaseHTTPRequestHandler, server: HermesApi) -> None:
        _send_sse(
            handler,
            _frame(1, "tool.started", tool="terminal", preview=server.api_key)
            + _frame(2, "run.completed", output="done"),
        )

    with HermesApi() as api:
        api.script(leaked)
        with _client(api) as client:
            events = list(client.iter_events(HERMES_RUN))
    assert events[0].payload["preview"] == "[redacted]"
    assert API_KEY not in json.dumps(events[0].payload)


def test_route_handlers_do_not_call_hermes() -> None:
    api = REPO / "src" / "deepfix_rag" / "api"
    for path in api.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "hermes_client" not in text
        assert "HermesClient" not in text
        assert "/v1/runs" not in text
    agent = (REPO / "src" / "deepfix_rag" / "agent_process.py").read_text(encoding="utf-8")
    assert "HermesClient" not in agent
    assert "hermes_client" not in agent
    loop = (REPO / "src" / "deepfix_rag" / "optimization.py").read_text(encoding="utf-8")
    assert "from deepfix_rag.hermes_client import" in loop


class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

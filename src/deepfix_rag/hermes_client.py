"""Hermes runs client.

One module starts, polls, stops, and streams a run on the Hermes API server
(``http://hermes:8642``). Route handlers do not call Hermes. The optimization
loop calls it only when ``OPTIMIZATION_AGENT`` is ``hermes``.

Every request sets a connect timeout and a read timeout. ``POST /v1/runs``
returns as soon as the run is accepted, so a hung socket fails in seconds
instead of waiting out the optimization timeout. The events stream is idle
only while Hermes is still sending ``: keepalive`` comments (about every 10
seconds). Silence longer than ``sse_idle_timeout`` is a stalled stream: the
client reconnects. A reconnect does not mark the run failed.

The bearer token is sent only on ``Authorization``. It is not written into
the run prompt, log lines, or exception text.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any
import urllib.parse

import httpx

from deepfix_rag.config.settings import ModelNotConfigured, settings
from deepfix_rag.run_bundle import RunBundleError, container_work_dir, validate_run_id

# Hermes writes a keepalive comment when an events stream has been idle for
# 10s (CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS on the pinned image). A live
# tool call still produces that comment. Silence past this window means the
# socket stalled.
DEFAULT_SSE_IDLE_TIMEOUT = 25.0
DEFAULT_CONNECT_TIMEOUT = 5.0
HERMES_HEALTH_TIMEOUT = 2.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_POOL_TIMEOUT = 5.0
HERMES_PROVIDER = "openrouter"
# The model id is configuration: Compose passes HERMES_MODEL and the sandbox
# writes the same value into its config.yaml. No default lives in this module.
HERMES_MODEL_ENV = "HERMES_MODEL"
HERMES_PROVIDER_ENV = "HERMES_MODEL_PROVIDER"
# Same cap the sandbox writes as ``agent.max_turns``. ``skill_view`` consumes
# turns, so the budget is the image value, not the old 15-step OpenHands line.
HERMES_MAX_TURNS = 40
HERMES_RUN_STATUSES = frozenset(
    {
        "started",
        "running",
        "stopping",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "unknown",
    }
)
HERMES_EVENT_NAMES = frozenset(
    {
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
    }
)
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.interrupted"})
_REDACTED = "[redacted]"

LOGGER = logging.getLogger(__name__)
# A DEBUG root logger must not dump request headers. The bearer token is one of them.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class HermesClientError(Exception):
    """Failure safe to show in the admin UI.

    Transport errors, conflicts, and a dropped events stream leave the run
    status to the caller. ``marks_run_failed`` stays false.
    """

    marks_run_failed: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class HermesConflict(HermesClientError):
    """HTTP 409. The client does not retry these."""


class HermesStreamError(HermesClientError):
    """The events stream dropped after retries. The run was not marked failed."""


class _StreamDropped(Exception):
    """The SSE socket ended or stalled before a terminal event."""


class _Unavailable(Exception):
    """HTTP 503. ``delay`` is the capped Retry-After wait in seconds."""

    def __init__(self, delay: float) -> None:
        super().__init__("unavailable")
        self.delay = delay


@dataclass(frozen=True)
class HermesRunAccepted:
    """``POST /v1/runs`` acceptance. ``hermes_run_id`` is Hermes's id, not DeepFix's."""

    hermes_run_id: str
    status: str
    replayed: bool


@dataclass(frozen=True)
class HermesRun:
    """One ``GET /v1/runs/{run_id}`` body, after unknown resolution."""

    run_id: str
    status: str
    output: str | None
    error: str | None
    admission_id: str | None = None
    execution_generation: int | None = None
    turn_exit_reason: str | None = None


@dataclass(frozen=True)
class HermesEvent:
    """One SSE data event, or one ``: keepalive`` comment."""

    name: str
    payload: dict[str, Any]
    seq: int | None = None


def optimization_instructions(run_id: str) -> str:
    """Fixed instructions for one optimization run.

    Names the dev file, the champion config, and the run directory inside
    the Hermes work directory. The text does not mention holdout, the product
    database, or any credential.
    """
    checked = validate_run_id(run_id)
    work = container_work_dir(checked).as_posix()
    dev = f"{work}/input/feedback_dev.jsonl"
    champion = f"{work}/input/champion.yaml"
    corpus = f"{work}/input/corpus"
    index = f"{work}/input/champion-index"
    ledger = f"{work}/input/ledger.jsonl"
    result = f"{work}/output/result.json"
    candidate = f"{work}/output/candidate.yaml"
    return (
        "Follow the optimization-loop skill by running deepfix with --json. "
        "Run the `deepfix` command on PATH. "
        "A direct call to the virtualenv interpreter does not write output/heartbeat.json. "
        "While a deepfix command runs, that file is rewritten every 30 seconds. "
        f"The DeepFix run id is {checked}. "
        "Use only the dense, hybrid, and bm25 retrieval backends. "
        f"The turn budget is {HERMES_MAX_TURNS} agent turns (agent.max_turns). "
        "Loading a skill counts as a turn. "
        "Exhausting that budget is a wrap-up, not a cancel: still write result.json. "
        "For query-time work (rerank, retrieval.top_k, fusion) follow the hpo-search skill: "
        "run `deepfix hpo` ONCE over configs/hpo_retrieval_small.yaml, then confirm the best "
        "trial on the full dev set. Do not hand-try candidates one at a time, and do not run "
        "a second sweep in this round. Chunking changes stay a single mutate-config plus a "
        "re-index, not a sweep. "
        "Do not run `deepfix run-diagnostic`. "
        "Do not promote a config and do not replace the champion config. "
        "Do not print secrets or environment variables. "
        f"The run directory is {work}. "
        f"Download the run bundle first by running `deepfix-sync download --run-id {checked}`. "
        f"The dev dataset is {dev}: pass it as `--dataset {dev}` to every `deepfix eval`. "
        "The hpo sweep uses a seeded 20-question subsample and does not take the dev dataset. "
        f"The champion config is {champion}. "
        f"The champion index directory is {index}. "
        f"The corpus directory is {corpus}. "
        f"Write the candidate config to {candidate}. "
        "Build the candidate index with `deepfix index --index-dir` set to the "
        f"candidate_index_uri that `deepfix-sync download` prints for run {checked}, "
        "only when the hypothesis changes an index-time key. "
        f"The ledger of earlier rounds is {ledger} when that file exists (read only). "
        "Skip a hypothesis whose skill and changed keys are already in that ledger "
        "for this champion. "
        "Recommend a candidate only when its primary metric gains at least 0.025 "
        "over the champion on the full dev set and no other retrieval metric drops. "
        "A sweep measures many configs internally; you still recommend at most one. "
        "If the confirmed candidate does not clear the gate, do not try another: write "
        "result.json with candidate_config_path null and finish. "
        f"Write {result} early, as soon as the hpo call has a best trial, so a turn-cap "
        "wrap-up still finds a valid file; finalize it after the confirmation. "
        f"It is JSON with the keys "
        "candidate_config_path (a path or null), primary_metric, summary, "
        "and mlflow_run_id (a string or null). "
        "Set candidate_config_path to null when you do not recommend a change. "
        "A null candidate still needs champion metrics from a completed baseline "
        "eval. If that eval does not complete, write output/state.json with "
        'status "blocked", save the eval JSON (including its error) as '
        "output/baseline_smoke.json, and put that error in summary. "
        f"Then upload with `deepfix-sync upload --run-id {checked} --result {result}`. "
        f"When candidate_config_path is a path, also pass `--candidate {candidate}`. "
        "The task is complete only when that result file exists with those keys."
    )


def hermes_health_ok(
    base_url: str,
    api_server_key: str | None = None,
    *,
    timeout: float = HERMES_HEALTH_TIMEOUT,
) -> bool:
    """True when ``GET /health`` returns HTTP 200 and ``{"status": "ok"}``.

    The key is not sent. Health is unauthenticated. The key is used only to
    refuse a base URL that already contains it.
    """
    try:
        origin = _check_base_url(base_url, api_server_key or "")
    except HermesClientError:
        return False
    try:
        response = httpx.get(
            f"{origin}/health",
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("status") == "ok"


def hermes_agent_available(base_url: str, api_server_key: str | None) -> bool:
    """True when ``API_SERVER_KEY`` is set and ``GET /health`` succeeds."""
    if not isinstance(api_server_key, str):
        return False
    try:
        _visible_secret(api_server_key)
    except HermesClientError:
        return False
    return hermes_health_ok(base_url, api_server_key)


def hermes_model_id(source: Mapping[str, str] | None = None) -> str:
    """Model id Hermes runs with, from ``HERMES_MODEL``.

    ``source`` defaults to the process environment, which ``Settings`` also
    reads. An empty value raises ``ModelNotConfigured`` naming the variable.
    """
    env = os.environ if source is None else source
    raw = env.get(HERMES_MODEL_ENV)
    value = raw if raw is not None else settings.HERMES_MODEL
    text = (value or "").strip()
    if not text:
        raise ModelNotConfigured(
            f"{HERMES_MODEL_ENV} is not set. Set it in the environment "
            "(see example.env) to the model id the Hermes sandbox runs."
        )
    return text


def hermes_provider(source: Mapping[str, str] | None = None) -> str:
    """Provider segment Hermes routes through. ``HERMES_MODEL_PROVIDER`` wins."""
    env = os.environ if source is None else source
    raw = env.get(HERMES_PROVIDER_ENV)
    value = raw if raw is not None else settings.HERMES_MODEL_PROVIDER
    return (value or "").strip() or HERMES_PROVIDER


class HermesClient:
    """HTTP client for the Hermes runs API.

    ``base_url`` is the API origin, for example ``http://hermes:8642``.
    ``api_server_key`` is sent only as ``Authorization: Bearer``. ``model``
    and ``provider`` are what ``POST /v1/runs`` submits; by default they come
    from ``HERMES_MODEL`` and ``HERMES_MODEL_PROVIDER``.
    """

    def __init__(
        self,
        base_url: str,
        api_server_key: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT,
        sse_idle_timeout: float = DEFAULT_SSE_IDLE_TIMEOUT,
        status_retries: int = 2,
        stream_retries: int = 2,
        retry_after_cap: float = 30.0,
    ) -> None:
        self._api_server_key = _visible_secret(api_server_key)
        self.base_url = _check_base_url(base_url, self._api_server_key)
        # Resolved on use, not on construction: stop/recover paths build a
        # client for a run that is already submitted and never need the id.
        self._model = (model or "").strip()
        self._provider = (provider or "").strip()
        self.connect_timeout = _positive("connect_timeout", connect_timeout)
        self.read_timeout = _positive("read_timeout", read_timeout)
        self.write_timeout = _positive("write_timeout", write_timeout)
        self.pool_timeout = _positive("pool_timeout", pool_timeout)
        self.sse_idle_timeout = _positive("sse_idle_timeout", sse_idle_timeout)
        self._status_retries = _nonnegative("status_retries", status_retries)
        self._stream_retries = _nonnegative("stream_retries", stream_retries)
        self._retry_after_cap = _positive("retry_after_cap", retry_after_cap)
        self._json_timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )
        self._sse_timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.sse_idle_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=self._json_timeout,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
        )

    def __enter__(self) -> HermesClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def model(self) -> str:
        """Model id posted with ``POST /v1/runs``. ``HERMES_MODEL`` unless given."""
        return self._model or hermes_model_id()

    @property
    def provider(self) -> str:
        """Provider segment posted with ``POST /v1/runs``."""
        return self._provider or hermes_provider()

    def start_run(self, run_id: str) -> HermesRunAccepted:
        """``POST /v1/runs``. ``run_id`` is the DeepFix id and the idempotency key.

        A replay (HTTP 202 and ``Idempotency-Replayed: true``) returns the
        original Hermes run id.
        """
        checked = _path_id(run_id)
        instructions = optimization_instructions(checked)
        self._reject_secret_text(instructions)
        payload = {
            "input": instructions,
            "instructions": instructions,
            "provider": self.provider,
            "model": self.model,
        }
        response = self._send(
            "POST",
            "/v1/runs",
            json_body=payload,
            headers={"Idempotency-Key": checked},
        )
        if response.status_code != 202:
            raise HermesClientError(
                f"Hermes POST /v1/runs returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        body = _json_object(response)
        hermes_run_id = body.get("run_id")
        status = body.get("status")
        if not isinstance(hermes_run_id, str) or not hermes_run_id:
            raise HermesClientError("Hermes POST /v1/runs did not return a run_id")
        if not isinstance(status, str) or not status:
            raise HermesClientError("Hermes POST /v1/runs did not return a status")
        replayed = response.headers.get("Idempotency-Replayed", "").strip().lower() == "true"
        return HermesRunAccepted(
            hermes_run_id=hermes_run_id,
            status=status,
            replayed=replayed,
        )

    def get_run(self, hermes_run_id: str) -> HermesRun:
        """``GET /v1/runs/{run_id}``.

        Status strings are returned as Hermes sent them. ``unknown`` is the
        exception: the client reads ``admission_id`` and ``execution_generation``,
        posts ``/resolve-unknown``, and returns ``interrupted``.
        """
        checked = _path_id(hermes_run_id)
        response = self._send("GET", f"/v1/runs/{checked}")
        run = self._parse_run(checked, _json_object(response))
        if run.status != "unknown":
            return run
        return self._resolve_unknown(run)

    def stop_run(self, hermes_run_id: str) -> str:
        """``POST /v1/runs/{run_id}/stop``. Returns the status string."""
        checked = _path_id(hermes_run_id)
        response = self._send("POST", f"/v1/runs/{checked}/stop")
        status = _json_object(response).get("status")
        if not isinstance(status, str) or not status:
            raise HermesClientError("Hermes stop response did not include a status")
        return status

    def iter_events(self, hermes_run_id: str) -> Iterator[HermesEvent]:
        """``GET /v1/runs/{run_id}/events``.

        Yields each SSE event and each ``: keepalive`` comment. A dropped or
        stalled stream is retried from the last event id. The retry itself
        does not mark the run failed; if the retries are exhausted this raises
        ``HermesStreamError``.
        """
        checked = _path_id(hermes_run_id)
        cursor: dict[str, int | None] = {"seq": None}
        drops_left = self._stream_retries
        unavailable_left = self._status_retries
        while True:
            failure: HermesClientError | None = None
            wait = 0.0
            try:
                yield from self._iter_once(checked, cursor)
                return
            except _StreamDropped:
                if drops_left <= 0:
                    failure = HermesStreamError(
                        "Hermes event stream dropped; the run was not marked failed",
                        code="stream_dropped",
                    )
                else:
                    drops_left -= 1
                    LOGGER.warning(
                        "hermes GET /v1/runs/%s/events stalled; retrying",
                        checked,
                    )
            except _Unavailable as unavailable:
                if unavailable_left <= 0:
                    failure = HermesClientError(
                        "Hermes events returned 503",
                        status_code=503,
                        code="unavailable",
                    )
                else:
                    unavailable_left -= 1
                    wait = unavailable.delay
                    LOGGER.warning(
                        "hermes GET /v1/runs/%s/events returned 503; retrying after %.1fs",
                        checked,
                        wait,
                    )
            if failure is not None:
                raise failure
            if wait > 0:
                time.sleep(wait)

    def _resolve_unknown(self, run: HermesRun) -> HermesRun:
        if not run.admission_id or run.execution_generation is None:
            raise HermesClientError(
                "Hermes reported unknown without admission_id and execution_generation",
                code="unknown_unresolved",
            )
        body = {
            "admission_id": run.admission_id,
            "execution_generation": run.execution_generation,
        }
        response = self._send(
            "POST",
            f"/v1/runs/{_path_id(run.run_id)}/resolve-unknown",
            json_body=body,
        )
        error = run.error
        output = run.output
        try:
            resolved = response.json()
        except json.JSONDecodeError:
            resolved = None
        if isinstance(resolved, dict):
            if isinstance(resolved.get("error"), str):
                error = self._public(resolved["error"])
            if isinstance(resolved.get("output"), str):
                output = self._public(resolved["output"])
        return HermesRun(
            run_id=run.run_id,
            status="interrupted",
            output=output,
            error=error,
            admission_id=run.admission_id,
            execution_generation=run.execution_generation,
            turn_exit_reason=run.turn_exit_reason,
        )

    def _iter_once(
        self, hermes_run_id: str, cursor: dict[str, int | None]
    ) -> Iterator[HermesEvent]:
        headers = {"Accept": "text/event-stream"}
        last_seq = cursor["seq"]
        if last_seq is not None:
            headers["Last-Event-ID"] = str(last_seq)
        dropped = False
        try:
            with self._http.stream(
                "GET",
                f"/v1/runs/{hermes_run_id}/events",
                headers=self._auth_headers(headers),
                timeout=self._sse_timeout,
            ) as response:
                self._check_stream_status(response)
                parser = _SseParser()
                for line in response.iter_lines():
                    event, bad_frame = _next_frame(parser, line)
                    if bad_frame:
                        dropped = True
                        break
                    if event is None:
                        continue
                    event = self._redact_event(event)
                    if event.seq is not None and last_seq is not None and event.seq <= last_seq:
                        continue
                    if event.seq is not None:
                        cursor["seq"] = event.seq
                        last_seq = event.seq
                    yield event
                    if event.name in _TERMINAL_EVENTS:
                        return
                if not dropped:
                    event, bad_frame = _next_frame(parser, None)
                    if bad_frame:
                        dropped = True
                    elif event is not None:
                        event = self._redact_event(event)
                        if event.seq is not None:
                            cursor["seq"] = event.seq
                        yield event
                        if event.name in _TERMINAL_EVENTS:
                            return
        except httpx.TimeoutException:
            pass
        except httpx.HTTPError:
            pass
        # Timeout, a broken socket, or a clean EOF with no terminal event.
        # The caller retries. That retry does not mark the run failed.
        raise _StreamDropped()

    def _check_stream_status(self, response: httpx.Response) -> None:
        if response.status_code == 503:
            delay = self._retry_delay(response)
            response.read()
            raise _Unavailable(delay)
        if response.status_code == 200:
            return
        response.read()
        self._raise_for_status(response)
        raise HermesClientError(
            f"Hermes events returned HTTP {response.status_code}",
            status_code=response.status_code,
        )

    def _parse_run(self, hermes_run_id: str, payload: Mapping[str, Any]) -> HermesRun:
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise HermesClientError("Hermes run status did not include a status string")
        returned = payload.get("run_id", hermes_run_id)
        if not isinstance(returned, str) or not returned:
            returned = hermes_run_id
        output = payload.get("output")
        error = payload.get("error")
        turn_exit_reason = payload.get("turn_exit_reason")
        return HermesRun(
            run_id=returned,
            status=status,
            output=self._public(output) if isinstance(output, str) else None,
            error=self._public(error) if isinstance(error, str) else None,
            admission_id=_admission_id(payload.get("admission_id")),
            execution_generation=_generation(payload.get("execution_generation")),
            turn_exit_reason=(
                self._public(turn_exit_reason) if isinstance(turn_exit_reason, str) else None
            ),
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        remaining = self._status_retries
        while True:
            response = self._request(method, path, json_body=json_body, headers=headers)
            if response.status_code != 503 or remaining <= 0:
                self._raise_for_status(response)
                return response
            remaining -= 1
            delay = self._retry_delay(response)
            LOGGER.warning(
                "hermes %s %s returned 503; retrying after %.1fs",
                method,
                path,
                delay,
            )
            time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if json_body is not None:
            self._reject_secret_text(json.dumps(json_body, ensure_ascii=False))
        failure: HermesClientError | None = None
        response: httpx.Response | None = None
        # One extra attempt for a reset connection. Timeouts are not retried:
        # the read timeout already elapsed, and a second wait would hide a hang.
        for attempt in range(2):
            try:
                response = self._http.request(
                    method,
                    path,
                    json=dict(json_body) if json_body is not None else None,
                    headers=self._auth_headers(headers),
                    timeout=self._json_timeout,
                )
            except httpx.TimeoutException:
                failure = HermesClientError(
                    f"Hermes {method} {path} timed out",
                    code="timeout",
                )
                response = None
                break
            except httpx.HTTPError as exc:
                failure = HermesClientError(
                    f"Hermes {method} {path} could not connect ({type(exc).__name__})",
                    code="transport",
                )
                response = None
                if attempt == 0:
                    LOGGER.warning("hermes %s %s transport failed; retrying once", method, path)
                    continue
            else:
                failure = None
                break
        if failure is not None or response is None:
            raise (
                failure
                if failure is not None
                else HermesClientError(
                    f"Hermes {method} {path} could not connect",
                    code="transport",
                )
            )
        LOGGER.info("hermes %s %s -> %s", method, path, response.status_code)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        code, message = _error_parts(response)
        code = self._public(code)
        message = self._public(message)
        text = f"Hermes returned {response.status_code} {code}"
        if message and message != code:
            text = f"{text}: {message}"
        text = self._public(text)
        if response.status_code == 409:
            raise HermesConflict(text, status_code=409, code=code)
        raise HermesClientError(text, status_code=response.status_code, code=code)

    def _retry_delay(self, response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After", "").strip()
        try:
            delay = float(raw)
        except ValueError:
            delay = 1.0
        if delay < 0:
            delay = 0.0
        if delay > self._retry_after_cap:
            return self._retry_after_cap
        return delay

    def _auth_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_server_key}"}
        if not extra:
            return headers
        for name, value in extra.items():
            if name.lower() == "authorization":
                raise HermesClientError("refusing to replace the Authorization header")
            if self._api_server_key in name or self._api_server_key in value:
                raise HermesClientError("refusing to send API_SERVER_KEY outside Authorization")
            headers[name] = value
        return headers

    def _reject_secret_text(self, text: str) -> None:
        if self._api_server_key in text:
            raise HermesClientError(
                "refusing to send API_SERVER_KEY in the request body or prompt"
            )

    def _public(self, text: str) -> str:
        cleaned = text.replace(self._api_server_key, _REDACTED)
        if self._api_server_key in cleaned:
            return "Hermes request failed"
        return cleaned

    def _redact_event(self, event: HermesEvent) -> HermesEvent:
        return HermesEvent(
            name=self._public(event.name),
            payload=_redact_value(event.payload, self._api_server_key),
            seq=event.seq,
        )


class _SseParser:
    """Parse one Hermes events stream.

    On the pinned image a frame is ``id: <seq>`` plus ``data:`` JSON whose
    ``event`` field is the name (``tool.started``, ``run.completed``, ...).
    There is no SSE ``event:`` line. ``: keepalive`` is a comment frame.
    ``: open`` and ``: stream closed`` are ignored.
    """

    def __init__(self) -> None:
        self._data: list[str] = []
        self._event_name: str | None = None
        self._event_id: str | None = None

    def feed(self, line: str) -> HermesEvent | None:
        line = line.rstrip("\r")
        if line.startswith(":"):
            comment = line[1:]
            if comment.startswith(" "):
                comment = comment[1:]
            if comment == "keepalive" or comment.startswith("keepalive "):
                return HermesEvent(name="keepalive", payload={}, seq=None)
            return None
        if line == "":
            return self._finish()
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            self._data.append(value)
            return None
        if line.startswith("event:"):
            value = line[6:]
            if value.startswith(" "):
                value = value[1:]
            self._event_name = value
            return None
        if line.startswith("id:"):
            value = line[3:]
            if value.startswith(" "):
                value = value[1:]
            self._event_id = value
            return None
        return None

    def flush(self) -> HermesEvent | None:
        if not self._data and self._event_name is None:
            return None
        return self._finish()

    def _finish(self) -> HermesEvent | None:
        raw = "\n".join(self._data)
        event_name = self._event_name
        event_id = self._event_id
        self._data = []
        self._event_name = None
        self._event_id = None
        if not raw and not event_name:
            return None
        payload: dict[str, Any] = {}
        if raw:
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("malformed SSE data") from exc
            if not isinstance(decoded, dict):
                raise ValueError("SSE data is not an object")
            payload = decoded
        name = payload.get("event")
        if not isinstance(name, str) or not name:
            name = event_name
        if not isinstance(name, str) or not name:
            return None
        seq = _seq(event_id)
        if seq is None:
            seq = _seq_value(payload.get("seq"))
        return HermesEvent(name=name, payload=payload, seq=seq)


def _next_frame(parser: _SseParser, line: str | None) -> tuple[HermesEvent | None, bool]:
    failed = False
    event: HermesEvent | None = None
    try:
        event = parser.flush() if line is None else parser.feed(line)
    except ValueError:
        failed = True
    if failed:
        return None, True
    return event, False


def _json_object(response: httpx.Response) -> dict[str, Any]:
    invalid = False
    body: Any = None
    try:
        body = response.json()
    except json.JSONDecodeError:
        invalid = True
    if invalid:
        raise HermesClientError(
            f"Hermes returned a non-JSON body (HTTP {response.status_code})",
            status_code=response.status_code,
            code="invalid_json",
        )
    if not isinstance(body, dict):
        raise HermesClientError(
            "Hermes returned a JSON value that is not an object",
            status_code=response.status_code,
        )
    return body


def _error_parts(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return "http_error", ""
    if not isinstance(payload, dict):
        return "http_error", ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return "http_error", ""
    code = error.get("code") or "http_error"
    message = error.get("message") or ""
    return str(code), str(message)


def _redact_value(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, _REDACTED)
    if isinstance(value, dict):
        return {str(key): _redact_value(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secret) for item in value]
    return value


def _path_id(run_id: str) -> str:
    try:
        return validate_run_id(run_id)
    except RunBundleError:
        pass
    raise HermesClientError("run id is not a single path segment")


def _visible_secret(api_server_key: str) -> str:
    if not isinstance(api_server_key, str) or not api_server_key.strip():
        raise HermesClientError("API_SERVER_KEY is empty")
    if api_server_key != api_server_key.strip():
        raise HermesClientError("API_SERVER_KEY must not have surrounding whitespace")
    if any(ord(char) < 33 or ord(char) > 126 for char in api_server_key):
        raise HermesClientError("API_SERVER_KEY must be visible ASCII without spaces")
    return api_server_key


def _check_base_url(base_url: str, api_server_key: str) -> str:
    if not isinstance(base_url, str):
        raise HermesClientError("Hermes base URL must be an http or https URL")
    # An empty key is a substring of every URL. Callers that have no key yet
    # pass "" and only the shape of the URL is checked.
    if api_server_key and api_server_key in base_url:
        raise HermesClientError("Hermes base URL must not contain API_SERVER_KEY")
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HermesClientError("Hermes base URL must be an http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HermesClientError(
            "Hermes base URL must not include credentials, a query, or a fragment"
        )
    return base_url.strip().rstrip("/")


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise HermesClientError(f"{name} must be a positive number of seconds")
    return float(value)


def _nonnegative(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HermesClientError(f"{name} must be a non-negative integer")
    return value


def _admission_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _generation(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _seq(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _seq_value(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value

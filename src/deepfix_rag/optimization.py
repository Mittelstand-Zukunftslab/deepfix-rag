"""Optimization runs owned by the API.

The same function starts a scheduled run and a manual one. It exports a feedback
snapshot, skips when too little is labeled, and otherwise launches one agent.
``OPTIMIZATION_AGENT=openhands`` (the default) starts the OpenHands child.
``hermes`` stages the run bundle, submits it with ``HermesClient``, and copies
``output/result.json`` back before ``finish_run``. Holdout scoring and promotion
stay in this process either way.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from deepfix_rag.agent_process import (
    AGENT_UNAVAILABLE_MESSAGE,
    AgentRunResult,
    AgentUnavailable,
    interpreter_imports_agent,
    run_agent_job,
)
from deepfix_rag.config.settings import LOGGER, Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import export_feedback
from deepfix_rag.hermes_client import (
    HermesClient,
    HermesClientError,
    HermesRun,
    hermes_agent_available,
)
from deepfix_rag.hermes_sync import bucket_from_env, download_result, store_from_env
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.promotion import PromotionDecision, apply_candidate
from deepfix_rag.run_bundle import RunBundleError, stage_run_bundle
from deepfix_rag.storage import connect


class OptimizationAgent(StrEnum):
    """Which sandbox ``start_optimization_run`` launches. The default is OpenHands."""

    OPENHANDS = "openhands"
    HERMES = "hermes"


RunStatus = Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
RunTrigger = Literal["cron", "manual"]
INTERRUPTED_REASON = "Interrupted: backend restarted while the run was in progress"
HERMES_STATUS_POLL_SECONDS = 15.0
HERMES_UNAVAILABLE_MESSAGE = "Hermes is not set up on this server, so a run cannot start."
OPENHANDS_STATUS_MESSAGE = (
    "OpenHands is not set up on this server, so a run cannot start. "
    "The schedule and history are still shown."
)
HERMES_STATUS_MESSAGE = (
    "Hermes is not set up on this server, so a run cannot start. "
    "The schedule and history are still shown."
)
_HERMES_IN_PROGRESS = frozenset({"started", "running", "stopping", "queued"})
STALE_REASON = "Stale: the run exceeded the configured timeout."
# The child is already killed at the run timeout. The grace covers the bookkeeping
# that follows, so a live finish is not marked stale in the same moment.
STALE_GRACE_SECONDS = 15 * 60
_TIMED_OUT = "Timed out"
_INVALID_RESULT = "The run did not write a valid result."

_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


class RunInProgress(Exception):
    """Another optimization run is already running."""

    def __init__(self) -> None:
        super().__init__("A run is already in progress.")


class AgentResultFile(BaseModel):
    """The only outcome the API reads from the agent."""

    model_config = ConfigDict(extra="ignore")

    candidate_config_path: str | None
    primary_metric: str
    summary: str
    mlflow_run_id: str | None


@dataclass(frozen=True)
class OptimizationRun:
    """One row from the run table."""

    run_id: str
    status: RunStatus
    trigger: RunTrigger
    reason: str | None
    primary_metric: str | None
    snapshot_id: str
    dev_count: int
    new_dev_count: int
    holdout_count: int
    candidate_config_path: str | None
    mlflow_run_id: str | None
    started_at: str
    ended_at: str | None
    dev_delta: dict[str, float] | None = None
    holdout_delta: dict[str, float] | None = None
    holdout_wins: int | None = None
    holdout_losses: int | None = None


@dataclass(frozen=True)
class OptimizationSettings:
    """Thresholds and the child-process inputs for one attempt."""

    min_dev_questions: int = 50
    min_new_dev_questions: int = 10
    min_holdout_questions: int = 15
    min_primary_delta: float = 0.025
    timeout_seconds: float = 3 * 60 * 60
    goal_rounds: int = 3
    interpreter: Path | None = None
    model: str = "openai/gpt-6-luna-pro"
    base_url: str | None = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    runner_script: Path | None = None
    source_env: Mapping[str, str] | None = None
    api_bin: Path | None = None
    private_paths: tuple[str, ...] = ()
    agent: OptimizationAgent = OptimizationAgent.OPENHANDS
    hermes_base_url: str = "http://hermes:8642"
    hermes_api_key: str | None = None
    hermes_status_poll_seconds: float = HERMES_STATUS_POLL_SECONDS


def settings_from(
    settings: Settings, source_env: Mapping[str, str] | None = None
) -> OptimizationSettings:
    """Copy the server thresholds and hide the product database from the agent."""
    from deepfix_rag.agent_process import openrouter_settings

    env = os.environ if source_env is None else source_env
    model, base_url, api_key = openrouter_settings(env)
    raw_interpreter = settings.OPENHANDS_PYTHON
    return OptimizationSettings(
        min_dev_questions=settings.OPTIMIZATION_MIN_DEV_QUESTIONS,
        min_new_dev_questions=settings.OPTIMIZATION_MIN_NEW_DEV_QUESTIONS,
        min_holdout_questions=settings.OPTIMIZATION_MIN_HOLDOUT_QUESTIONS,
        min_primary_delta=settings.OPTIMIZATION_MIN_PRIMARY_DELTA,
        timeout_seconds=settings.OPTIMIZATION_RUN_TIMEOUT_HOURS * 60 * 60,
        goal_rounds=settings.OPTIMIZATION_GOAL_ROUNDS,
        interpreter=Path(raw_interpreter) if raw_interpreter else None,
        model=model,
        base_url=base_url,
        api_key=api_key,
        source_env=env,
        private_paths=(str(settings.PRODUCT_DATA_DIR), str(settings.product_db_path)),
        agent=OptimizationAgent(settings.OPTIMIZATION_AGENT),
        hermes_base_url=settings.HERMES_BASE_URL.strip().rstrip("/"),
        hermes_api_key=settings.API_SERVER_KEY,
    )


def skill_path() -> Path:
    """The optimization-loop skill shipped with this repository."""
    return (
        Path(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "optimization-loop"
        / "SKILL.md"
    )


def skills_dir() -> Path:
    """Directory that holds the optimization-loop skill and the pathology skills."""
    return skill_path().parent.parent


def optimization_prompt(
    *,
    run_id: str,
    workspace: Path,
    dev_path: Path,
    min_primary_delta: float,
) -> str:
    """Instructions for one run. Holdout is never mentioned."""
    run_dir = workspace / "runs" / run_id
    return (
        f"Follow the procedure in {skill_path()} by running deepfix with --json. "
        f"The pathology skills it routes to are in the same directory tree: {skills_dir()}. "
        f"Run every command from the workspace directory {workspace}, which is your current "
        "directory, with plain `deepfix` (not `uv run deepfix`). "
        "Use only the dense, hybrid, or bm25 retrieval backends. "
        f"The dev dataset is {dev_path}: pass it as `--dataset {dev_path}` to every "
        "`deepfix eval`. "
        f"The champion config is {workspace / 'configs' / 'champion.yaml'}. "
        f"The champion index directory is {workspace / 'indexes' / 'current'}. "
        f"The corpus directory is {workspace / 'corpus'}. "
        f"The run directory is {run_dir}. Write candidate configs and candidate indexes "
        f"only under it, for example {run_dir / 'candidate.yaml'} and {run_dir / 'idx'}. "
        "Do not promote a config and do not replace the champion config. "
        f"Recommend a candidate only when its primary metric gains at least "
        f"{min_primary_delta:g} over the champion on the full dev set and no other "
        "retrieval metric drops. "
        f"The ledger of earlier rounds is {workspace / 'runs' / 'ledger.jsonl'} (read only). "
        "Skip a hypothesis whose skill and changed keys are already in that ledger for "
        "this champion. "
        f"After each command, write {run_dir / 'state.json'} as JSON with the keys "
        "step, skill, commands, and status. "
        "Test one hypothesis in this run. If it does not clear the gate, do not try "
        "another: write result.json with candidate_config_path null and finish. "
        f"Finish by writing {run_dir / 'result.json'} as JSON with the keys "
        "candidate_config_path (a path or null), primary_metric, summary, "
        "and mlflow_run_id (a string or null). "
        "Set candidate_config_path to null when you do not recommend a change. "
        "The task is complete only when that file exists with those keys and this "
        "conversation shows the deepfix eval output that primary_metric comes from "
        "and the output of printing that file."
    )


def init_optimization(db_path: Path) -> None:
    """Create the run table if it does not exist."""
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS optimization_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(
                    status IN ('running', 'skipped', 'rejected', 'promoted', 'succeeded', 'failed')
                ),
                trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('cron', 'manual')),
                reason TEXT,
                primary_metric TEXT,
                snapshot_id TEXT NOT NULL,
                dev_count INTEGER NOT NULL,
                new_dev_count INTEGER NOT NULL,
                holdout_count INTEGER NOT NULL,
                dev_query_ids TEXT NOT NULL,
                candidate_config_path TEXT,
                mlflow_run_id TEXT,
                summary TEXT,
                pid INTEGER,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                dev_delta TEXT,
                holdout_delta TEXT,
                holdout_wins INTEGER,
                holdout_losses INTEGER
            )
            """
        )
        _ensure_score_columns(conn)


def start_optimization_run(
    db_path: Path,
    workspace: Path,
    *,
    trigger: RunTrigger,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None = None,
) -> OptimizationRun:
    """Export feedback, then skip, refuse, or start one background agent run."""
    init_optimization(db_path)
    prepare_workspace(workspace)
    exported = export_feedback(db_path, workspace)
    dev_ids = sorted(question.query_id for question in exported.dev)
    run_id = uuid.uuid4().hex
    started_at = _now()
    # A row left running by a dead thread must not block the next manual or nightly start.
    fail_stale_runs(db_path, timeout_seconds=options.timeout_seconds)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _running_id(conn) is not None:
            raise RunInProgress()
        previous = _previous_dev_ids(conn)
        new_count = len(dev_ids) if previous is None else len(set(dev_ids) - previous)
        reason = _skip_reason(
            len(dev_ids),
            new_count,
            len(exported.holdout),
            options,
        )
        if reason is None and not _can_launch(options):
            raise AgentUnavailable(_launch_blocked_message(options))
        status: RunStatus = "skipped" if reason is not None else "running"
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, reason, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at, ended_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                status,
                trigger,
                reason,
                exported.snapshot_id,
                len(dev_ids),
                new_count,
                len(exported.holdout),
                json.dumps(dev_ids),
                started_at,
                started_at if status == "skipped" else None,
            ),
        )
    if status == "running":
        _start_thread(
            db_path,
            workspace,
            run_id,
            exported.dev_path,
            options,
            coordinator,
            holdout_queries=tuple(question.query for question in exported.holdout),
            holdout_query_ids=tuple(question.query_id for question in exported.holdout),
        )
    return get_run(db_path, run_id)


def recent_runs(db_path: Path, limit: int = 20) -> list[OptimizationRun]:
    """Return the newest runs, newest first."""
    init_optimization(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM optimization_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_run_from_row(row) for row in rows]


def mlflow_run_url(run_id: str | None) -> str | None:
    """Link for an HTTP tracking server. A local SQLite store has no page to open."""
    if not run_id:
        return None
    try:
        import mlflow

        tracking = mlflow.get_tracking_uri()
        if not tracking.startswith("http"):
            return None
        experiment_id = mlflow.get_run(run_id).info.experiment_id
    except Exception:
        LOGGER.debug("Could not build an MLflow URL for %s", run_id, exc_info=True)
        return None
    return f"{tracking.rstrip('/')}/#/experiments/{experiment_id}/runs/{run_id}"


def get_run(db_path: Path, run_id: str) -> OptimizationRun:
    """Return one stored run."""
    init_optimization(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM optimization_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise KeyError(run_id)
    return _run_from_row(row)


def finish_run(
    db_path: Path,
    workspace: Path,
    run_id: str,
    *,
    runner_error: str | None,
    timed_out: bool,
    coordinator: IndexCoordinator | None = None,
    min_primary_delta: float = 0.025,
    private_paths: tuple[str, ...] = (),
) -> None:
    """Apply result.json, then append one ledger row for the round."""
    try:
        _finish_run_impl(
            db_path,
            workspace,
            run_id,
            runner_error=runner_error,
            timed_out=timed_out,
            coordinator=coordinator,
            min_primary_delta=min_primary_delta,
            private_paths=private_paths,
        )
    finally:
        _record_ledger(db_path, workspace, run_id)


def _finish_run_impl(
    db_path: Path,
    workspace: Path,
    run_id: str,
    *,
    runner_error: str | None,
    timed_out: bool,
    coordinator: IndexCoordinator | None = None,
    min_primary_delta: float = 0.025,
    private_paths: tuple[str, ...] = (),
) -> None:
    """Apply result.json. A candidate is scored here and is not trusted on its own."""
    if timed_out:
        _finish(db_path, run_id, status="failed", reason=_TIMED_OUT)
        return
    result = _read_result(workspace / "runs" / run_id / "result.json")
    if result is None:
        _finish(db_path, run_id, status="failed", reason=runner_error or _INVALID_RESULT)
        return
    if result.candidate_config_path is None:
        _finish(
            db_path,
            run_id,
            status="succeeded",
            reason=result.summary,
            primary_metric=result.primary_metric,
            candidate_config_path=None,
            mlflow_run_id=result.mlflow_run_id,
            summary=result.summary,
        )
        return
    stored = get_run(db_path, run_id)
    decision = apply_candidate(
        db_path,
        workspace,
        snapshot_id=stored.snapshot_id,
        candidate_config_path=result.candidate_config_path,
        primary_metric=result.primary_metric,
        summary=result.summary,
        mlflow_run_id=result.mlflow_run_id,
        min_primary_delta=min_primary_delta,
        coordinator=coordinator,
        private_paths=private_paths,
    )
    _store_decision(db_path, run_id, decision)


def fail_interrupted(db_path: Path) -> int:
    """Fail runs left running by a restart and stop any child pid this process recorded.

    The row is committed before the pid is signaled. A reused or missing pid must not
    keep the run in ``running`` or take down this process.
    """
    init_optimization(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, pid FROM optimization_runs WHERE status = 'running'"
        ).fetchall()
    stuck = [(str(row["run_id"]), _pid(row["pid"])) for row in rows]
    return _fail_selected(db_path, stuck, INTERRUPTED_REASON)


def fail_stale_runs(
    db_path: Path,
    *,
    timeout_seconds: float,
    now: datetime | None = None,
) -> int:
    """Fail ``running`` rows older than the run timeout plus ``STALE_GRACE_SECONDS``.

    Questions on those rows stay consumed, the same as any other failed run.
    """
    init_optimization(db_path)
    moment = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    limit_seconds = timeout_seconds + STALE_GRACE_SECONDS
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_id, pid, started_at FROM optimization_runs
            WHERE status = 'running'
            """
        ).fetchall()
    stale = [
        (str(row["run_id"]), _pid(row["pid"]))
        for row in rows
        if _older_than(row["started_at"], moment, limit_seconds)
    ]
    return _fail_selected(db_path, stale, STALE_REASON)


def wait_for_run(run_id: str, timeout: float = 30) -> None:
    """Block until the background agent thread for this run has exited."""
    with _threads_lock:
        thread = _threads.get(run_id)
    if thread is None:
        return
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Run {run_id} is still running.")


def run_hermes_job(
    workspace: Path,
    run_id: str,
    *,
    dev_path: Path,
    options: OptimizationSettings,
    holdout_queries: tuple[str, ...] = (),
    holdout_query_ids: tuple[str, ...] = (),
) -> AgentRunResult:
    """Stage the bundle, submit the run, and copy ``output/result.json`` locally.

    The caller passes that file to ``finish_run``. This function does not score
    holdout and does not promote. Hermes prose is discarded.

    Cancel, the stall window, ``stop_run`` on timeout, event lines, and restart
    recovery that asks Hermes before failing a row are later work. A poll that
    passes ``timeout_seconds`` returns ``timed_out`` so the row can finish. It
    does not call ``stop_run``.
    """
    env = options.source_env if options.source_env is not None else os.environ
    store = store_from_env(env)
    bucket = bucket_from_env(env)
    stage_run_bundle(
        run_id=run_id,
        workspace=workspace,
        dev_path=dev_path,
        store=store,
        bucket=bucket,
        holdout_queries=holdout_queries,
        holdout_query_ids=holdout_query_ids,
    )
    secret = options.hermes_api_key
    with HermesClient(options.hermes_base_url, secret or "") as client:
        accepted = client.start_run(run_id)
        deadline = time.monotonic() + options.timeout_seconds
        current = _poll_hermes(client, accepted.hermes_run_id, deadline, options, run_id)
        if current is None:
            return AgentRunResult(final_response=None, error=None, timed_out=True, returncode=None)
        download_result(
            store,
            bucket=bucket,
            run_id=run_id,
            destination=workspace / "runs" / run_id / "result.json",
        )
        if current.status == "completed":
            return AgentRunResult(final_response=None, error=None, timed_out=False, returncode=0)
        reason = current.error or f"The Hermes run ended with status {current.status}."
        return AgentRunResult(
            final_response=None,
            error=_redact(reason, secret),
            timed_out=False,
            returncode=1,
        )


def _poll_hermes(
    client: HermesClient,
    hermes_run_id: str,
    deadline: float,
    options: OptimizationSettings,
    run_id: str,
) -> HermesRun | None:
    """Poll until the run leaves the in-progress statuses, or the deadline passes."""
    while True:
        if time.monotonic() >= deadline:
            return None
        try:
            current = client.get_run(hermes_run_id)
        except HermesClientError as exc:
            LOGGER.warning("optimization run=%s hermes status poll failed: %s", run_id, exc)
        else:
            if current.status not in _HERMES_IN_PROGRESS:
                return current
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(_poll_delay(options, remaining))


def _poll_delay(options: OptimizationSettings, remaining: float) -> float:
    interval = options.hermes_status_poll_seconds
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        interval = HERMES_STATUS_POLL_SECONDS
    return min(float(interval), remaining)


def _start_hermes_thread(
    db_path: Path,
    workspace: Path,
    run_id: str,
    dev_path: Path,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
    *,
    holdout_queries: tuple[str, ...],
    holdout_query_ids: tuple[str, ...],
) -> None:
    def _target() -> None:
        try:
            outcome = run_hermes_job(
                workspace,
                run_id,
                dev_path=dev_path,
                options=options,
                holdout_queries=holdout_queries,
                holdout_query_ids=holdout_query_ids,
            )
        except Exception as exc:
            _finish(db_path, run_id, status="failed", reason=_hermes_failure(exc, options))
            return
        try:
            finish_run(
                db_path,
                workspace,
                run_id,
                runner_error=outcome.error,
                timed_out=outcome.timed_out,
                coordinator=coordinator,
                min_primary_delta=options.min_primary_delta,
                private_paths=options.private_paths,
            )
        except Exception as exc:
            LOGGER.warning(
                "Optimization run %s failed while finishing (%s)",
                run_id,
                type(exc).__name__,
            )
            _finish(db_path, run_id, status="failed", reason=_hermes_failure(exc, options))

    thread = threading.Thread(target=_target, name=f"optimization-{run_id}", daemon=True)
    with _threads_lock:
        _threads[run_id] = thread
    thread.start()


def _hermes_failure(exc: Exception, options: OptimizationSettings) -> str:
    """Admin-visible text. Unknown failures do not include the exception string."""
    if isinstance(exc, (HermesClientError, RunBundleError)):
        text = str(exc).strip() or "The Hermes run could not be started."
    else:
        text = "The Hermes run could not be started."
    return _redact(text, options.hermes_api_key)


def _redact(text: str, secret: str | None) -> str:
    if secret:
        return text.replace(secret, "[redacted]")
    return text


def _launch_blocked_message(options: OptimizationSettings) -> str:
    if options.agent is OptimizationAgent.HERMES:
        return HERMES_UNAVAILABLE_MESSAGE
    return AGENT_UNAVAILABLE_MESSAGE


def _start_thread(
    db_path: Path,
    workspace: Path,
    run_id: str,
    dev_path: Path,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
    *,
    holdout_queries: tuple[str, ...] = (),
    holdout_query_ids: tuple[str, ...] = (),
) -> None:
    if options.agent is OptimizationAgent.HERMES:
        _start_hermes_thread(
            db_path,
            workspace,
            run_id,
            dev_path,
            options,
            coordinator,
            holdout_queries=holdout_queries,
            holdout_query_ids=holdout_query_ids,
        )
        return
    interpreter = options.interpreter
    if interpreter is None:
        raise AgentUnavailable(AGENT_UNAVAILABLE_MESSAGE)

    def _target() -> None:
        try:
            outcome = run_agent_job(
                workspace,
                run_id,
                prompt=optimization_prompt(
                    run_id=run_id,
                    workspace=workspace,
                    dev_path=dev_path,
                    min_primary_delta=options.min_primary_delta,
                ),
                model=options.model,
                base_url=options.base_url,
                api_key=options.api_key,
                interpreter=interpreter,
                goal_rounds=options.goal_rounds,
                timeout_seconds=options.timeout_seconds,
                runner_script=options.runner_script,
                source_env=options.source_env,
                api_bin=options.api_bin,
                private_paths=options.private_paths,
                on_start=lambda pid: _set_pid(db_path, run_id, pid),
            )
        except Exception as exc:
            _finish(
                db_path, run_id, status="failed", reason=str(exc) or "The agent runner failed."
            )
            return
        try:
            finish_run(
                db_path,
                workspace,
                run_id,
                runner_error=outcome.error,
                timed_out=outcome.timed_out,
                coordinator=coordinator,
                min_primary_delta=options.min_primary_delta,
                private_paths=options.private_paths,
            )
        except Exception as exc:
            LOGGER.exception("Optimization run %s failed while finishing", run_id)
            _finish(
                db_path, run_id, status="failed", reason=str(exc) or "The agent runner failed."
            )

    thread = threading.Thread(target=_target, name=f"optimization-{run_id}", daemon=True)
    with _threads_lock:
        _threads[run_id] = thread
    thread.start()


def _skip_reason(
    dev_count: int,
    new_count: int,
    holdout_count: int,
    options: OptimizationSettings,
) -> str | None:
    parts: list[str] = []
    if dev_count < options.min_dev_questions:
        parts.append(f"{dev_count} labeled questions in dev, {options.min_dev_questions} needed")
    if new_count < options.min_new_dev_questions:
        parts.append(
            f"{new_count} new labeled questions in dev, {options.min_new_dev_questions} needed"
        )
    if holdout_count < options.min_holdout_questions:
        parts.append(
            f"{holdout_count} labeled questions in holdout, {options.min_holdout_questions} needed"
        )
    if not parts:
        return None
    return "Skipped: " + ". ".join(parts) + "."


def _interpreter_ready(interpreter: Path | None) -> bool:
    return interpreter is not None and interpreter.is_file()


def _can_launch(options: OptimizationSettings) -> bool:
    """True when this process can start the agent the run will use."""
    if options.agent is OptimizationAgent.HERMES:
        return agent_available(options)
    if not _interpreter_ready(options.interpreter):
        return False
    if not options.api_key or not options.base_url or not options.model:
        return False
    if options.runner_script is not None:
        return options.runner_script.is_file()
    assert options.interpreter is not None
    return interpreter_imports_agent(options.interpreter)


_import_cache: dict[str, bool] = {}


def agent_status(options: OptimizationSettings) -> tuple[bool, str | None]:
    """Whether a run can start, and the admin sentence when it cannot."""
    if agent_available(options):
        return True, None
    if options.agent is OptimizationAgent.HERMES:
        return False, HERMES_STATUS_MESSAGE
    return False, OPENHANDS_STATUS_MESSAGE


def agent_available(options: OptimizationSettings) -> bool:
    """True when the selected agent can be started.

    OpenHands checks the interpreter, and that import check is cached.
    Hermes is true when ``API_SERVER_KEY`` is set and ``GET /health`` succeeds.
    """
    if options.agent is OptimizationAgent.HERMES:
        return hermes_agent_available(options.hermes_base_url, options.hermes_api_key)
    if not _interpreter_ready(options.interpreter) or options.interpreter is None:
        return False
    if not options.api_key or not options.base_url or not options.model:
        return False
    key = str(options.interpreter)
    if key not in _import_cache:
        _import_cache[key] = interpreter_imports_agent(options.interpreter)
    return _import_cache[key]


def _running_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM optimization_runs WHERE status = 'running' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return str(row["run_id"])


def _previous_dev_ids(conn: sqlite3.Connection) -> set[str] | None:
    row = conn.execute(
        """
        SELECT dev_query_ids FROM optimization_runs
        WHERE status != 'skipped'
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    loaded = json.loads(row["dev_query_ids"])
    return set(loaded) if isinstance(loaded, list) else set()


def _read_result(path: Path) -> AgentResultFile | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AgentResultFile.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError):
        return None


def _record_ledger(db_path: Path, workspace: Path, run_id: str) -> None:
    """Append one round to runs/ledger.jsonl. Holdout numbers are not written."""
    try:
        stored = get_run(db_path, run_id)
    except KeyError:
        return
    if stored.status == "running":
        return
    state_path = workspace / "runs" / run_id / "state.json"
    state: dict = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            state = loaded
    champion = workspace / "configs" / "champion.yaml"
    champion_hash = (
        hashlib.sha256(champion.read_bytes()).hexdigest() if champion.is_file() else None
    )
    keys = state.get("keys") if isinstance(state.get("keys"), list) else []
    row = {
        "run_id": run_id,
        "skill": state.get("skill"),
        "keys": keys,
        "dev_delta": stored.dev_delta,
        "decision": stored.status,
        "champion_sha256": champion_hash,
        "primary_metric": stored.primary_metric,
        "snapshot_id": stored.snapshot_id,
    }
    path = workspace / "runs" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def _set_pid(db_path: Path, run_id: str, pid: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE optimization_runs SET pid = ? WHERE run_id = ? AND status = 'running'",
            (pid, run_id),
        )


def _fail_selected(
    db_path: Path,
    rows: list[tuple[str, int | None]],
    reason: str,
) -> int:
    """Commit each failure, then signal only that run's recorded process group."""
    failed = 0
    for run_id, pid in rows:
        if not _finish(db_path, run_id, status="failed", reason=reason):
            continue
        failed += 1
        LOGGER.warning("Optimization run %s marked failed: %s", run_id, reason)
        _kill_pid(pid)
    return failed


def _pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _older_than(started_at: object, moment: datetime, limit_seconds: float) -> bool:
    if not isinstance(started_at, str):
        return False
    started = _parse_time(started_at)
    if started is None:
        return False
    return (moment - started).total_seconds() > limit_seconds


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _kill_pid(pid: int | None) -> None:
    """Stop the recorded child process group and no other process."""
    if pid is None or pid <= 0 or pid == os.getpid() or pid == os.getpgrp():
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (OSError, OverflowError):
        return


def _store_decision(db_path: Path, run_id: str, decision: PromotionDecision) -> None:
    status: RunStatus = (
        decision.status if decision.status in {"rejected", "promoted", "failed"} else "failed"
    )
    _finish(
        db_path,
        run_id,
        status=status,
        reason=decision.reason,
        primary_metric=decision.primary_metric,
        candidate_config_path=decision.candidate_config_path,
        mlflow_run_id=decision.mlflow_run_id,
        summary=decision.summary,
        dev_delta=decision.dev_delta,
        holdout_delta=decision.holdout_delta,
        holdout_wins=decision.holdout_wins,
        holdout_losses=decision.holdout_losses,
    )


def _finish(
    db_path: Path,
    run_id: str,
    *,
    status: RunStatus,
    reason: str,
    primary_metric: str | None = None,
    candidate_config_path: str | None = None,
    mlflow_run_id: str | None = None,
    summary: str | None = None,
    dev_delta: dict[str, float] | None = None,
    holdout_delta: dict[str, float] | None = None,
    holdout_wins: int | None = None,
    holdout_losses: int | None = None,
) -> bool:
    """Mark one running row finished. Returns whether this call changed it."""
    with connect(db_path) as conn:
        updated = conn.execute(
            """
            UPDATE optimization_runs
            SET status = ?, reason = ?, primary_metric = ?, candidate_config_path = ?,
                mlflow_run_id = ?, summary = ?, ended_at = ?, pid = NULL,
                dev_delta = ?, holdout_delta = ?, holdout_wins = ?, holdout_losses = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (
                status,
                reason,
                primary_metric,
                candidate_config_path,
                mlflow_run_id,
                summary,
                _now(),
                _dump_delta(dev_delta),
                _dump_delta(holdout_delta),
                holdout_wins,
                holdout_losses,
                run_id,
            ),
        )
        return updated.rowcount > 0


def _run_from_row(row: sqlite3.Row) -> OptimizationRun:
    return OptimizationRun(
        run_id=row["run_id"],
        status=row["status"],
        trigger=row["trigger_kind"],
        reason=row["reason"],
        primary_metric=row["primary_metric"],
        snapshot_id=row["snapshot_id"],
        dev_count=row["dev_count"],
        new_dev_count=row["new_dev_count"],
        holdout_count=row["holdout_count"],
        candidate_config_path=row["candidate_config_path"],
        mlflow_run_id=row["mlflow_run_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        dev_delta=_load_delta(row["dev_delta"]),
        holdout_delta=_load_delta(row["holdout_delta"]),
        holdout_wins=row["holdout_wins"],
        holdout_losses=row["holdout_losses"],
    )


def _ensure_score_columns(conn: sqlite3.Connection) -> None:
    existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(optimization_runs)")}
    for name, column_type in (
        ("dev_delta", "TEXT"),
        ("holdout_delta", "TEXT"),
        ("holdout_wins", "INTEGER"),
        ("holdout_losses", "INTEGER"),
    ):
        if name not in existing:
            conn.execute(f"ALTER TABLE optimization_runs ADD COLUMN {name} {column_type}")


def _dump_delta(delta: dict[str, float] | None) -> str | None:
    if delta is None:
        return None
    return json.dumps(delta)


def _load_delta(raw: str | None) -> dict[str, float] | None:
    if raw is None:
        return None
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return None
    return {str(key): float(value) for key, value in loaded.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

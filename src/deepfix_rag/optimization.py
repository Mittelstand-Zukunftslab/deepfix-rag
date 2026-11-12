"""Optimization runs owned by the API.

The same function starts a scheduled run and a manual one. It exports a feedback
snapshot, skips when too little is labeled, and otherwise launches one agent.
``OPTIMIZATION_AGENT=openhands`` (the default) starts the OpenHands child.
``hermes`` stages the run bundle, submits it with ``HermesClient``, and follows
``GET /v1/runs/{id}/events`` plus a status poll. ``output/result.json`` is copied
back before ``finish_run``. Holdout scoring and promotion stay in this process.
"""

from collections.abc import Callable, Mapping
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
    HermesEvent,
    HermesRun,
    HermesStreamError,
    hermes_agent_available,
)
from deepfix_rag.hermes_sync import (
    bucket_from_env,
    download_result,
    heartbeat_timestamp,
    read_heartbeat,
    store_from_env,
)
from deepfix_rag.hermes_trace_experiment import (
    HERMES_TRACE_EXPERIMENT,
    ensure_hermes_trace_experiment,
)
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.promotion import PromotionDecision, apply_candidate
from deepfix_rag.run_bundle import (
    ObjectStore,
    RunBundleError,
    stage_run_bundle,
    validate_run_id,
)
from deepfix_rag.storage import connect


class OptimizationAgent(StrEnum):
    """Which sandbox ``start_optimization_run`` launches. The default is OpenHands."""

    OPENHANDS = "openhands"
    HERMES = "hermes"


RunStatus = Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
RunTrigger = Literal["cron", "manual"]
INTERRUPTED_REASON = "Interrupted: backend restarted while the run was in progress"
HERMES_INTERRUPTED_REASON = "Interrupted: Hermes restarted while the run was in progress"
CANCELLED_REASON = "Cancelled"
HERMES_STATUS_POLL_SECONDS = 15.0
HERMES_STOP_WAIT_SECONDS = 120.0
HEARTBEAT_STALL_MINUTES = 5.0
STEP_PREVIEW_LIMIT = 500
OUTPUT_LOG_LIMIT = 64 * 1024
_STEP_LOG_DIRNAME = "optimization-logs"
_HEARTBEAT_FIELDS = ("run_id", "pid", "elapsed_s", "argv0", "written_at")
_SECRET_ENVS = (
    "API_SERVER_KEY",
    "OPENROUTER_API_KEY",
    "S3_SECRET_ACCESS_KEY",
    "HERMES_S3_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
)
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


class RunNotRunning(Exception):
    """Cancel was asked for a row that is not running."""

    def __init__(self) -> None:
        super().__init__("This run is not in progress.")


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
    hermes_run_id: str | None = None
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
    heartbeat_stall_seconds: float = HEARTBEAT_STALL_MINUTES * 60
    hermes_stop_wait_seconds: float = HERMES_STOP_WAIT_SECONDS
    step_log_dir: Path | None = None


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
        heartbeat_stall_seconds=_stall_seconds(settings.OPTIMIZATION_HEARTBEAT_STALL_MINUTES),
        step_log_dir=Path(settings.PRODUCT_DATA_DIR) / _STEP_LOG_DIRNAME,
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
                hermes_run_id TEXT,
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
    fail_stale_runs(
        db_path,
        timeout_seconds=options.timeout_seconds,
        options=options,
    )
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
    if not _row_is_running(db_path, run_id):
        return
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
    if not _row_is_running(db_path, run_id):
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
    options: OptimizationSettings | None = None,
) -> int:
    """Fail ``running`` rows older than the run timeout plus ``STALE_GRACE_SECONDS``.

    Questions on those rows stay consumed, the same as any other failed run.
    OpenHands rows still use the recorded pid. A Hermes row is also asked to stop
    when the flag is ``hermes``.
    """
    init_optimization(db_path)
    moment = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    limit_seconds = timeout_seconds + STALE_GRACE_SECONDS
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_id, pid, hermes_run_id, started_at FROM optimization_runs
            WHERE status = 'running'
            """
        ).fetchall()
    stale = [row for row in rows if _older_than(row["started_at"], moment, limit_seconds)]
    selected = [(str(row["run_id"]), _pid(row["pid"])) for row in stale]
    failed = _fail_selected(db_path, selected, STALE_REASON)
    hermes_ids = [
        (str(row["run_id"]), str(row["hermes_run_id"]))
        for row in stale
        if isinstance(row["hermes_run_id"], str) and row["hermes_run_id"]
    ]
    if options is not None and options.agent is OptimizationAgent.HERMES and hermes_ids:
        _stop_stored(options, hermes_ids)
    return failed


def wait_for_run(run_id: str, timeout: float = 30) -> None:
    """Block until the background agent thread for this run has exited."""
    with _threads_lock:
        thread = _threads.get(run_id)
    if thread is None:
        return
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Run {run_id} is still running.")


def stall_reason(stall_seconds: float) -> str:
    """Failure text for a quiet run. The number is the configured window, in minutes."""
    minutes = stall_seconds / 60
    if minutes == int(minutes):
        label = str(int(minutes))
    else:
        label = f"{minutes:g}"
    return f"Stalled: no heartbeat for {label} minutes."


def run_hermes_job(
    workspace: Path,
    run_id: str,
    *,
    db_path: Path,
    dev_path: Path,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None = None,
    holdout_queries: tuple[str, ...] = (),
    holdout_query_ids: tuple[str, ...] = (),
) -> None:
    """Stage the bundle, submit one Hermes run, and follow it until the row settles.

    ``Idempotency-Key`` is the DeepFix ``run_id``. This function does not score
    holdout and does not promote. Hermes prose is discarded. ``completed`` copies
    ``output/result.json`` and calls ``finish_run``.
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
    if not _row_is_running(db_path, run_id):
        return
    # The gateway reads the id baked at boot. Creating the experiment here
    # covers a boot that ran before MLflow was up, so the next gateway start
    # and any search for deepfix_hermes find the same experiment.
    if ensure_hermes_trace_experiment() is None:
        LOGGER.warning("hermes trace experiment %s was not created", HERMES_TRACE_EXPERIMENT)
    secret = options.hermes_api_key or ""
    started = _started_at(db_path, run_id)
    with HermesClient(options.hermes_base_url, secret) as client:
        accepted = client.start_run(run_id)
        _set_hermes_run_id(db_path, run_id, accepted.hermes_run_id)
        if not _row_is_running(db_path, run_id):
            _stop_quietly(client, accepted.hermes_run_id, run_id)
            return
        _follow_hermes_run(
            client,
            db_path=db_path,
            workspace=workspace,
            run_id=run_id,
            hermes_run_id=accepted.hermes_run_id,
            options=options,
            store=store,
            bucket=bucket,
            coordinator=coordinator,
            deadline=_deadline_from(started, options.timeout_seconds),
        )


def recover_running_runs(
    db_path: Path,
    workspace: Path,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None = None,
) -> None:
    """Settle rows left ``running`` by a restart.

    OpenHands still fails those rows and signals the recorded pid. Hermes asks
    about the stored id first. An unreachable Hermes leaves the row ``running``
    until ``fail_stale_runs``.
    """
    if options.agent is not OptimizationAgent.HERMES:
        fail_interrupted(db_path)
        return
    init_optimization(db_path)
    prepare_workspace(workspace)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_id, pid, hermes_run_id, started_at FROM optimization_runs
            WHERE status = 'running'
            """
        ).fetchall()
    for row in rows:
        run_id = str(row["run_id"])
        hermes_run_id = row["hermes_run_id"] if isinstance(row["hermes_run_id"], str) else None
        if hermes_run_id:
            _recover_hermes_row(
                db_path,
                workspace,
                run_id,
                hermes_run_id,
                started_at=row["started_at"],
                options=options,
                coordinator=coordinator,
            )
            continue
        pid = _pid(row["pid"])
        if pid is not None:
            _fail_selected(db_path, [(run_id, pid)], INTERRUPTED_REASON)
            _record_ledger(db_path, workspace, run_id)
            continue
        _adopt_unassigned_hermes_run(
            db_path,
            workspace,
            run_id,
            started_at=row["started_at"],
            options=options,
            coordinator=coordinator,
        )


def cancel_optimization_run(
    db_path: Path,
    workspace: Path,
    run_id: str,
    options: OptimizationSettings,
) -> OptimizationRun:
    """Stop one running row. Hermes rows call ``stop_run``. OpenHands rows kill the child."""
    init_optimization(db_path)
    current = get_run(db_path, run_id)
    if current.status != "running":
        raise RunNotRunning()
    _signal_cancel(run_id)
    hermes_run_id = current.hermes_run_id
    pid = _read_pid(db_path, run_id)
    if not _finish(db_path, run_id, status="failed", reason=CANCELLED_REASON):
        raise RunNotRunning()
    _record_ledger(db_path, workspace, run_id)
    LOGGER.warning("Optimization run %s marked failed: %s", run_id, CANCELLED_REASON)
    if hermes_run_id:
        _stop_stored(options, [(run_id, hermes_run_id)])
    _kill_pid(pid)
    return get_run(db_path, run_id)


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
            run_hermes_job(
                workspace,
                run_id,
                db_path=db_path,
                dev_path=dev_path,
                options=options,
                coordinator=coordinator,
                holdout_queries=holdout_queries,
                holdout_query_ids=holdout_query_ids,
            )
        except Exception as exc:
            _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))

    _start_registered_thread(run_id, _target)


def _hermes_failure(exc: Exception, options: OptimizationSettings) -> str:
    """Admin-visible text. Unknown failures do not include the exception string."""
    if isinstance(exc, (HermesClientError, RunBundleError)):
        text = str(exc).strip() or "The Hermes run could not be started."
    else:
        text = "The Hermes run could not be started."
    return _redact(text, _secrets(options))


def _secrets(options: OptimizationSettings) -> tuple[str, ...]:
    """Credential values and private paths that must not reach a log or a reason."""
    env = options.source_env if options.source_env is not None else os.environ
    found: list[str] = []
    key = options.hermes_api_key
    if isinstance(key, str) and len(key.strip()) >= 8:
        found.append(key.strip())
    for name in _SECRET_ENVS:
        value = env.get(name)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if len(value) >= 8 and value not in found:
            found.append(value)
    for path in options.private_paths:
        if len(path) >= 8 and path not in found:
            found.append(path)
    found.sort(key=len, reverse=True)
    return tuple(found)


def _redact(text: str, secret: str | None | tuple[str, ...] = None) -> str:
    if not secret:
        return text
    values = (secret,) if isinstance(secret, str) else secret
    for item in sorted((value for value in values if value), key=len, reverse=True):
        text = text.replace(item, "[redacted]")
    return text


@dataclass
class _EventProgress:
    """Stall clock shared by the events reader and the status loop."""

    last_progress: float
    lock: threading.Lock
    wake: threading.Event
    closed: threading.Event
    stream_done: threading.Event
    last_heartbeat_token: str | None = None


def _follow_hermes_run(
    client: HermesClient,
    *,
    db_path: Path,
    workspace: Path,
    run_id: str,
    hermes_run_id: str,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    coordinator: IndexCoordinator | None,
    deadline: float,
) -> None:
    """Read events and poll status until the DeepFix row leaves ``running``."""
    progress = _EventProgress(
        last_progress=time.monotonic(),
        lock=threading.Lock(),
        wake=threading.Event(),
        closed=threading.Event(),
        stream_done=threading.Event(),
    )
    _note_fresh_heartbeat(progress, store, bucket, run_id, options)
    _register_wake(run_id, progress.wake)
    reader = threading.Thread(
        target=_read_hermes_events,
        args=(client, progress, run_id, hermes_run_id, options),
        name=f"optimization-events-{run_id}",
        daemon=True,
    )
    reader.start()
    try:
        _watch_hermes_status(
            client,
            progress,
            db_path=db_path,
            workspace=workspace,
            run_id=run_id,
            hermes_run_id=hermes_run_id,
            options=options,
            store=store,
            bucket=bucket,
            coordinator=coordinator,
            deadline=deadline,
        )
    finally:
        progress.closed.set()
        progress.wake.set()
        client.close()
        reader.join(timeout=5)
        _clear_wake(run_id)


def _watch_hermes_status(
    client: HermesClient,
    progress: _EventProgress,
    *,
    db_path: Path,
    workspace: Path,
    run_id: str,
    hermes_run_id: str,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    coordinator: IndexCoordinator | None,
    deadline: float,
) -> None:
    next_poll = time.monotonic()
    heartbeat_checked = 0.0
    heartbeat_age: float | None = None
    last_observe = 0.0
    while True:
        if not _row_is_running(db_path, run_id):
            return
        now = time.monotonic()
        if now - last_observe >= _poll_interval(options):
            _observe_heartbeat(
                progress,
                options,
                store,
                bucket,
                run_id,
                hermes_run_id,
            )
            last_observe = now
        if now >= deadline:
            _stop_and_wait(client, hermes_run_id, run_id, options, progress.wake)
            _capture_output(client, options, run_id, hermes_run_id)
            if _row_is_running(db_path, run_id):
                _fail_running(db_path, workspace, run_id, _TIMED_OUT)
            return
        stalled, heartbeat_age, heartbeat_checked = _stall_check(
            progress,
            options,
            store,
            bucket,
            run_id,
            now=now,
            heartbeat_age=heartbeat_age,
            heartbeat_checked=heartbeat_checked,
        )
        if stalled:
            _stop_quietly(client, hermes_run_id, run_id)
            _capture_output(client, options, run_id, hermes_run_id)
            if _row_is_running(db_path, run_id):
                _fail_running(
                    db_path,
                    workspace,
                    run_id,
                    stall_reason(_stall_window(options)),
                )
            return
        if progress.wake.is_set() or now >= next_poll:
            progress.wake.clear()
            if not _row_is_running(db_path, run_id):
                return
            if _poll_once(
                client,
                db_path=db_path,
                workspace=workspace,
                run_id=run_id,
                hermes_run_id=hermes_run_id,
                options=options,
                store=store,
                bucket=bucket,
                coordinator=coordinator,
            ):
                progress.stream_done.wait(2)
                return
            next_poll = time.monotonic() + _poll_interval(options)
        remaining = min(deadline, next_poll) - time.monotonic()
        _wait_slice(remaining, progress)


def _poll_once(
    client: HermesClient,
    *,
    db_path: Path,
    workspace: Path,
    run_id: str,
    hermes_run_id: str,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    coordinator: IndexCoordinator | None,
) -> bool:
    """Poll once. Return True when the DeepFix row was settled from this poll."""
    try:
        current = client.get_run(hermes_run_id)
    except HermesClientError as exc:
        LOGGER.warning("optimization run=%s hermes status poll failed: %s", run_id, exc)
        return False
    if current.status in _HERMES_IN_PROGRESS:
        return False
    if not _row_is_running(db_path, run_id):
        return True
    if _accepts_result_file(current):
        _append_terminal_output(options, run_id, hermes_run_id, current.output)
        _finish_completed(
            db_path,
            workspace,
            run_id,
            options,
            store,
            bucket,
            coordinator,
        )
        return True
    _append_terminal_output(options, run_id, hermes_run_id, current.output)
    _fail_running(db_path, workspace, run_id, _hermes_terminal_reason(current, options))
    return True


def _finish_completed(
    db_path: Path,
    workspace: Path,
    run_id: str,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    coordinator: IndexCoordinator | None,
) -> None:
    download_result(
        store,
        bucket=bucket,
        run_id=run_id,
        destination=workspace / "runs" / run_id / "result.json",
    )
    try:
        finish_run(
            db_path,
            workspace,
            run_id,
            runner_error=None,
            timed_out=False,
            coordinator=coordinator,
            min_primary_delta=options.min_primary_delta,
            private_paths=options.private_paths,
        )
    except Exception:
        LOGGER.warning(
            "Optimization run %s failed while finishing (%s)",
            run_id,
            "finish_run",
        )
        _fail_running(db_path, workspace, run_id, _INVALID_RESULT)


def _read_hermes_events(
    client: HermesClient,
    progress: _EventProgress,
    run_id: str,
    hermes_run_id: str,
    options: OptimizationSettings,
) -> None:
    try:
        for event in client.iter_events(hermes_run_id):
            if progress.closed.is_set():
                return
            if event.name == "keepalive":
                continue
            _write_step(options, run_id, hermes_run_id, event)
            with progress.lock:
                progress.last_progress = time.monotonic()
            progress.wake.set()
    except HermesStreamError:
        LOGGER.warning(
            "optimization run=%s hermes event stream dropped; the run was not marked failed",
            run_id,
        )
    except Exception as exc:
        LOGGER.warning(
            "optimization run=%s hermes event stream ended (%s)",
            run_id,
            type(exc).__name__,
        )
    finally:
        progress.stream_done.set()
        progress.wake.set()


def _write_step(
    options: OptimizationSettings,
    run_id: str,
    hermes_run_id: str,
    event: HermesEvent,
) -> None:
    line = _step_line(run_id, hermes_run_id, event, options)
    LOGGER.info("%s", line)
    _append_log(options, run_id, line + "\n")


def _step_line(
    run_id: str,
    hermes_run_id: str,
    event: HermesEvent,
    options: OptimizationSettings,
) -> str:
    secrets = _secrets(options)
    tool = event.payload.get("tool")
    tool_text = tool if isinstance(tool, str) else ""
    preview = _event_preview(event.payload)
    tool_text = _one_line(_redact(tool_text, secrets))
    preview = _one_line(_redact(preview, secrets))[:STEP_PREVIEW_LIMIT]
    name = _one_line(_redact(event.name, secrets))
    return (
        f"optimization run={run_id} hermes_run={hermes_run_id} "
        f"event={name} tool={tool_text} preview={preview}"
    )


def _append_log(options: OptimizationSettings, run_id: str, text: str) -> None:
    directory = options.step_log_dir
    if directory is None or not text:
        return
    try:
        checked = validate_run_id(run_id)
    except RunBundleError:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{checked}.log").open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    except OSError:
        LOGGER.warning("optimization run=%s step log could not be written", run_id)


def _append_terminal_output(
    options: OptimizationSettings,
    run_id: str,
    hermes_run_id: str,
    output: str | None,
) -> None:
    """Append the final Hermes output, truncated to 64 KiB, to the private log."""
    if not isinstance(output, str) or not output:
        return
    secrets = _secrets(options)
    text = _redact(output, secrets)
    raw = text.encode("utf-8")
    if len(raw) > OUTPUT_LOG_LIMIT:
        text = raw[:OUTPUT_LOG_LIMIT].decode("utf-8", errors="ignore")
    preview = _one_line(text)[:STEP_PREVIEW_LIMIT]
    line = (
        f"optimization run={run_id} hermes_run={hermes_run_id} "
        f"event=output tool= preview={preview}"
    )
    LOGGER.info("%s", line)
    _append_log(options, run_id, line + "\n----- output -----\n" + text + "\n")


def _capture_output(
    client: HermesClient,
    options: OptimizationSettings,
    run_id: str,
    hermes_run_id: str,
) -> None:
    try:
        current = client.get_run(hermes_run_id)
    except HermesClientError:
        return
    _append_terminal_output(options, run_id, hermes_run_id, current.output)


def _observe_heartbeat(
    progress: _EventProgress,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    run_id: str,
    hermes_run_id: str,
) -> None:
    """Log one heartbeat line when ``output/heartbeat.json`` moves.

    A healthy long command can be silent on the events stream. The line is
    what an operator sees on the API stderr log between tool events.
    """
    try:
        written = heartbeat_timestamp(store, bucket=bucket, run_id=run_id)
    except Exception:
        LOGGER.warning("optimization run=%s heartbeat read failed", run_id)
        return
    if written is None:
        return
    age = (datetime.now(timezone.utc) - written).total_seconds()
    window = _stall_window(options)
    if age < 0 or age >= window:
        return
    token = written.isoformat()
    with progress.lock:
        if progress.last_heartbeat_token == token:
            return
        progress.last_heartbeat_token = token
    try:
        payload = read_heartbeat(store, bucket=bucket, run_id=run_id) or {}
    except Exception:
        LOGGER.warning("optimization run=%s heartbeat read failed", run_id)
        payload = {}
    line = _heartbeat_line(run_id, hermes_run_id, payload, options)
    LOGGER.info("%s", line)
    _append_log(options, run_id, line + "\n")


def _heartbeat_line(
    run_id: str,
    hermes_run_id: str,
    payload: Mapping[str, object],
    options: OptimizationSettings,
) -> str:
    secrets = _secrets(options)
    parts: list[str] = []
    for key in _HEARTBEAT_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            continue
        parts.append(f"{key}={_one_line(_redact(text, secrets))}")
    preview = " ".join(parts)[:STEP_PREVIEW_LIMIT]
    return (
        f"optimization run={run_id} hermes_run={hermes_run_id} "
        f"event=heartbeat tool=deepfix preview={preview}"
    )


def _event_preview(payload: Mapping[str, object]) -> str:
    for key in ("preview", "text", "error", "output", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _stall_check(
    progress: _EventProgress,
    options: OptimizationSettings,
    store: ObjectStore,
    bucket: str,
    run_id: str,
    *,
    now: float,
    heartbeat_age: float | None,
    heartbeat_checked: float,
) -> tuple[bool, float | None, float]:
    window = _stall_window(options)
    with progress.lock:
        event_age = now - progress.last_progress
    if event_age < window:
        return False, heartbeat_age, heartbeat_checked
    if now - heartbeat_checked >= _poll_interval(options):
        heartbeat_age = _heartbeat_age(store, bucket, run_id)
        heartbeat_checked = now
    if heartbeat_age is None or heartbeat_age >= window:
        return True, heartbeat_age, heartbeat_checked
    return False, heartbeat_age, heartbeat_checked


def _heartbeat_age(store: ObjectStore, bucket: str, run_id: str) -> float | None:
    try:
        written = heartbeat_timestamp(store, bucket=bucket, run_id=run_id)
    except Exception:
        LOGGER.warning("optimization run=%s heartbeat read failed", run_id)
        return None
    if written is None:
        return None
    return (datetime.now(timezone.utc) - written).total_seconds()


def _note_fresh_heartbeat(
    progress: _EventProgress,
    store: ObjectStore,
    bucket: str,
    run_id: str,
    options: OptimizationSettings,
) -> None:
    age = _heartbeat_age(store, bucket, run_id)
    window = _stall_window(options)
    if age is None or age < 0 or age >= window:
        return
    with progress.lock:
        progress.last_progress = time.monotonic() - age


def _stop_and_wait(
    client: HermesClient,
    hermes_run_id: str,
    run_id: str,
    options: OptimizationSettings,
    wake: threading.Event,
) -> None:
    """``stop_run``, then wait until Hermes is terminal or the stop wait elapses."""
    _stop_quietly(client, hermes_run_id, run_id)
    deadline = time.monotonic() + _stop_wait(options)
    while time.monotonic() < deadline:
        try:
            current = client.get_run(hermes_run_id)
        except HermesClientError as exc:
            LOGGER.warning("optimization run=%s hermes status poll failed: %s", run_id, exc)
        else:
            if current.status not in _HERMES_IN_PROGRESS:
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        wake.wait(min(_poll_interval(options), remaining))


def _stop_quietly(client: HermesClient, hermes_run_id: str, run_id: str) -> None:
    try:
        client.stop_run(hermes_run_id)
    except HermesClientError as exc:
        LOGGER.warning("optimization run=%s hermes stop failed: %s", run_id, exc)


def _stop_stored(options: OptimizationSettings, pairs: list[tuple[str, str]]) -> None:
    if not pairs:
        return
    secret = options.hermes_api_key
    if not secret:
        LOGGER.warning("optimization hermes stop skipped: API_SERVER_KEY is not set")
        return
    try:
        client_cm = HermesClient(options.hermes_base_url, secret)
    except HermesClientError as exc:
        LOGGER.warning("optimization hermes stop skipped: %s", exc)
        return
    with client_cm as client:
        for run_id, hermes_run_id in pairs:
            _stop_quietly(client, hermes_run_id, run_id)


def _accepts_result_file(current: HermesRun) -> bool:
    """True when the row is settled from ``result.json``.

    ``completed`` is that path. Exhausting ``agent.max_turns`` is too, and it
    is not a cancel. On Hermes v0.22.7 the runs API stores that wrap-up as
    status ``failed`` with ``turn_exit_reason`` ``max_iterations_reached(N/N)``
    and ``completed: false`` (chat instead reports ``finish_reason`` ``length``
    and ``hermes.error_code`` ``iteration_budget_exhausted``). A generic
    ``failed`` status, a cancel, and an interrupt do not read the file.
    """
    if current.status == "completed":
        return True
    if current.status != "failed":
        return False
    reason = current.turn_exit_reason or ""
    return reason.startswith("max_iterations_reached(")


def _hermes_terminal_reason(current: HermesRun, options: OptimizationSettings) -> str:
    if current.error:
        return _redact(current.error, _secrets(options))
    if current.status == "cancelled":
        return CANCELLED_REASON
    if current.status == "interrupted":
        return HERMES_INTERRUPTED_REASON
    if current.status == "failed":
        return "The Hermes run ended with status failed."
    return f"The Hermes run ended with status {current.status}."


def _recover_hermes_row(
    db_path: Path,
    workspace: Path,
    run_id: str,
    hermes_run_id: str,
    *,
    started_at: object,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
) -> None:
    secret = options.hermes_api_key or ""
    try:
        client_cm = HermesClient(options.hermes_base_url, secret)
    except HermesClientError as exc:
        LOGGER.warning("optimization run=%s hermes lookup skipped: %s", run_id, exc)
        return
    with client_cm as client:
        try:
            current = client.get_run(hermes_run_id)
        except HermesClientError as exc:
            LOGGER.warning("optimization run=%s hermes lookup failed: %s", run_id, exc)
            return
        if current.status in _HERMES_IN_PROGRESS:
            _start_registered_thread(
                run_id,
                lambda: _resume_hermes_thread(
                    db_path,
                    workspace,
                    run_id,
                    hermes_run_id,
                    started_at=started_at,
                    options=options,
                    coordinator=coordinator,
                ),
            )
            return
        _settle_terminal_status(
            client,
            current,
            db_path=db_path,
            workspace=workspace,
            run_id=run_id,
            options=options,
            coordinator=coordinator,
        )


def _resume_hermes_thread(
    db_path: Path,
    workspace: Path,
    run_id: str,
    hermes_run_id: str,
    *,
    started_at: object,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
) -> None:
    env = options.source_env if options.source_env is not None else os.environ
    try:
        store = store_from_env(env)
        bucket = bucket_from_env(env)
    except RunBundleError as exc:
        _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))
        return
    secret = options.hermes_api_key or ""
    try:
        with HermesClient(options.hermes_base_url, secret) as client:
            _follow_hermes_run(
                client,
                db_path=db_path,
                workspace=workspace,
                run_id=run_id,
                hermes_run_id=hermes_run_id,
                options=options,
                store=store,
                bucket=bucket,
                coordinator=coordinator,
                deadline=_deadline_from(started_at, options.timeout_seconds),
            )
    except Exception as exc:
        _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))


def _adopt_unassigned_hermes_run(
    db_path: Path,
    workspace: Path,
    run_id: str,
    *,
    started_at: object,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
) -> None:
    """Ask Hermes for a run that never stored its id. The idempotency key is ``run_id``."""

    def _target() -> None:
        secret = options.hermes_api_key or ""
        try:
            with HermesClient(options.hermes_base_url, secret) as client:
                if not _row_is_running(db_path, run_id):
                    return
                accepted = client.start_run(run_id)
                _set_hermes_run_id(db_path, run_id, accepted.hermes_run_id)
                if not _row_is_running(db_path, run_id):
                    _stop_quietly(client, accepted.hermes_run_id, run_id)
                    return
                env = options.source_env if options.source_env is not None else os.environ
                store = store_from_env(env)
                bucket = bucket_from_env(env)
                _follow_hermes_run(
                    client,
                    db_path=db_path,
                    workspace=workspace,
                    run_id=run_id,
                    hermes_run_id=accepted.hermes_run_id,
                    options=options,
                    store=store,
                    bucket=bucket,
                    coordinator=coordinator,
                    deadline=_deadline_from(started_at, options.timeout_seconds),
                )
        except HermesClientError as exc:
            LOGGER.warning("optimization run=%s hermes lookup failed: %s", run_id, exc)
        except RunBundleError as exc:
            _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))
        except Exception as exc:
            _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))

    _start_registered_thread(run_id, _target)


def _settle_terminal_status(
    client: HermesClient,
    current: HermesRun,
    *,
    db_path: Path,
    workspace: Path,
    run_id: str,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
) -> None:
    del client
    if _accepts_result_file(current):
        env = options.source_env if options.source_env is not None else os.environ
        try:
            store = store_from_env(env)
            bucket = bucket_from_env(env)
        except RunBundleError as exc:
            _fail_running(db_path, workspace, run_id, _hermes_failure(exc, options))
            return
        _finish_completed(
            db_path,
            workspace,
            run_id,
            options,
            store,
            bucket,
            coordinator,
        )
        return
    _fail_running(db_path, workspace, run_id, _hermes_terminal_reason(current, options))


def _fail_running(db_path: Path, workspace: Path, run_id: str, reason: str) -> None:
    if not _finish(db_path, run_id, status="failed", reason=reason):
        return
    LOGGER.warning("Optimization run %s marked failed: %s", run_id, reason)
    _record_ledger(db_path, workspace, run_id)


def _start_registered_thread(run_id: str, target: Callable[[], None]) -> None:
    thread = threading.Thread(target=target, name=f"optimization-{run_id}", daemon=True)
    with _threads_lock:
        _threads[run_id] = thread
    thread.start()


def _register_wake(run_id: str, wake: threading.Event) -> None:
    with _threads_lock:
        _wakes[run_id] = wake


def _clear_wake(run_id: str) -> None:
    with _threads_lock:
        _wakes.pop(run_id, None)


def _signal_cancel(run_id: str) -> None:
    with _threads_lock:
        wake = _wakes.get(run_id)
    if wake is not None:
        wake.set()


def _wait_slice(seconds: float, progress: _EventProgress) -> None:
    if seconds <= 0:
        return
    end = time.monotonic() + seconds
    while not progress.closed.is_set():
        if progress.wake.is_set():
            return
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        progress.wake.wait(min(0.2, remaining))


def _poll_interval(options: OptimizationSettings) -> float:
    interval = options.hermes_status_poll_seconds
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        return HERMES_STATUS_POLL_SECONDS
    return float(interval)


def _stall_window(options: OptimizationSettings) -> float:
    window = options.heartbeat_stall_seconds
    if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
        return HEARTBEAT_STALL_MINUTES * 60
    return float(window)


def _stop_wait(options: OptimizationSettings) -> float:
    wait = options.hermes_stop_wait_seconds
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        return HERMES_STOP_WAIT_SECONDS
    return float(wait)


def _stall_seconds(minutes: float) -> float:
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return HEARTBEAT_STALL_MINUTES * 60
    return float(minutes) * 60


def _deadline_from(started_at: object, timeout_seconds: float) -> float:
    if not isinstance(started_at, str):
        return time.monotonic() + timeout_seconds
    started = _parse_time(started_at)
    if started is None:
        return time.monotonic() + timeout_seconds
    remaining = timeout_seconds - (datetime.now(timezone.utc) - started).total_seconds()
    if remaining < 0:
        remaining = 0
    return time.monotonic() + remaining


def _started_at(db_path: Path, run_id: str) -> str | None:
    try:
        return get_run(db_path, run_id).started_at
    except KeyError:
        return None


def _row_is_running(db_path: Path, run_id: str) -> bool:
    try:
        return get_run(db_path, run_id).status == "running"
    except KeyError:
        return False


def _set_hermes_run_id(db_path: Path, run_id: str, hermes_run_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE optimization_runs
            SET hermes_run_id = ?
            WHERE run_id = ? AND status = 'running' AND hermes_run_id IS NULL
            """,
            (hermes_run_id, run_id),
        )


def _read_pid(db_path: Path, run_id: str) -> int | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT pid FROM optimization_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return _pid(row["pid"])


_wakes: dict[str, threading.Event] = {}


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


# A round counts as the baseline only after it finishes. ``failed`` includes a
# cancel, a stall, a holdout-guard refusal, a crash, and an invalid result.
# Those runs, and ``skipped`` ones, must not spend the new-question budget.
_BASELINE_STATUSES = ("succeeded", "rejected", "promoted")


def _previous_dev_ids(conn: sqlite3.Connection) -> set[str] | None:
    """Dev question ids from the newest round that actually finished.

    The baseline is the latest ``succeeded``, ``rejected``, or ``promoted``
    run. A newer ``failed`` or ``skipped`` row is ignored, so a run that died
    before it produced a result — including one refused at the holdout upload
    guard — does not make those questions look already used.
    """
    placeholders = ", ".join("?" for _ in _BASELINE_STATUSES)
    row = conn.execute(
        f"""
        SELECT dev_query_ids FROM optimization_runs
        WHERE status IN ({placeholders})
        ORDER BY started_at DESC
        LIMIT 1
        """,
        _BASELINE_STATUSES,
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
    marker = f'"run_id": "{run_id}"'
    if path.is_file() and marker in path.read_text(encoding="utf-8"):
        return
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
        hermes_run_id=row["hermes_run_id"] if isinstance(row["hermes_run_id"], str) else None,
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
        ("hermes_run_id", "TEXT"),
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

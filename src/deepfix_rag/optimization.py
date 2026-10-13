"""Optimization runs owned by the API.

The same function starts a scheduled run and a manual one. It exports a feedback
snapshot, skips when too little is labeled, and otherwise launches one Hermes child.
Promotion is a later step: this module never replaces the champion.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from deepfix_rag.accounts import connect
from deepfix_rag.agent_process import HermesUnavailable, run_agent_job
from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import export_feedback

RunStatus = Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
RunTrigger = Literal["cron", "manual"]
_INTERRUPTED = "Interrupted by restart"
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


@dataclass(frozen=True)
class OptimizationSettings:
    """Thresholds and the child-process inputs for one attempt."""

    min_dev_questions: int = 50
    min_new_dev_questions: int = 10
    min_holdout_questions: int = 15
    min_primary_delta: float = 0.025
    timeout_seconds: float = 3 * 60 * 60
    interpreter: Path | None = None
    model: str = "openai/gpt-6-luna-pro"
    base_url: str | None = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    runner_script: Path | None = None
    source_env: Mapping[str, str] | None = None
    api_bin: Path | None = None
    private_paths: tuple[str, ...] = ()


def settings_from(
    settings: Settings, source_env: Mapping[str, str] | None = None
) -> OptimizationSettings:
    """Copy the server thresholds and hide the product database from the agent."""
    from deepfix_rag.agent_process import openrouter_settings

    env = os.environ if source_env is None else source_env
    model, base_url, api_key = openrouter_settings(env)
    raw_interpreter = settings.HERMES_PYTHON
    return OptimizationSettings(
        min_dev_questions=settings.OPTIMIZATION_MIN_DEV_QUESTIONS,
        min_new_dev_questions=settings.OPTIMIZATION_MIN_NEW_DEV_QUESTIONS,
        min_holdout_questions=settings.OPTIMIZATION_MIN_HOLDOUT_QUESTIONS,
        min_primary_delta=settings.OPTIMIZATION_MIN_PRIMARY_DELTA,
        timeout_seconds=settings.OPTIMIZATION_RUN_TIMEOUT_HOURS * 60 * 60,
        interpreter=Path(raw_interpreter) if raw_interpreter else None,
        model=model,
        base_url=base_url,
        api_key=api_key,
        source_env=env,
        private_paths=(str(settings.PRODUCT_DATA_DIR), str(settings.product_db_path)),
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


def optimization_prompt(
    *,
    run_id: str,
    workspace: Path,
    dev_path: Path,
) -> str:
    """Instructions for one run. Holdout is never mentioned."""
    run_dir = workspace / "runs" / run_id
    return (
        f"Follow the procedure in {skill_path()} by running deepfix with --json. "
        "Use only the dense, hybrid, or bm25 retrieval backends. "
        f"The dev dataset is {dev_path}. "
        f"The champion config is {workspace / 'configs' / 'champion.yaml'}. "
        f"The champion index directory is {workspace / 'indexes' / 'current'}. "
        f"The corpus directory is {workspace / 'corpus'}. "
        f"Write candidate configs and candidate indexes only under {run_dir}. "
        "Do not promote a config and do not replace the champion config. "
        f"Finish by writing {run_dir / 'result.json'} as JSON with the keys "
        "candidate_config_path (a path or null), primary_metric, summary, "
        "and mlflow_run_id (a string or null). "
        "Set candidate_config_path to null when you do not recommend a change."
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
                ended_at TEXT
            )
            """
        )


def start_optimization_run(
    db_path: Path,
    workspace: Path,
    *,
    trigger: RunTrigger,
    options: OptimizationSettings,
) -> OptimizationRun:
    """Export feedback, then skip, refuse, or start one background agent run."""
    init_optimization(db_path)
    prepare_workspace(workspace)
    exported = export_feedback(db_path, workspace)
    dev_ids = sorted(question.query_id for question in exported.dev)
    run_id = uuid.uuid4().hex
    started_at = _now()
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
        if reason is None and not _interpreter_ready(options.interpreter):
            raise HermesUnavailable("Hermes is not set up on this server, so a run cannot start.")
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
        _start_thread(db_path, workspace, run_id, exported.dev_path, options)
    return get_run(db_path, run_id)


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
) -> None:
    """Apply result.json. A timeout always fails the run, and the champion stays put."""
    if timed_out:
        _finish(db_path, run_id, status="failed", reason=_TIMED_OUT)
        return
    result = _read_result(workspace / "runs" / run_id / "result.json")
    if result is None:
        _finish(db_path, run_id, status="failed", reason=runner_error or _INVALID_RESULT)
        return
    _finish(
        db_path,
        run_id,
        status="succeeded",
        reason=result.summary,
        primary_metric=result.primary_metric,
        candidate_config_path=result.candidate_config_path,
        mlflow_run_id=result.mlflow_run_id,
        summary=result.summary,
    )


def fail_interrupted(db_path: Path) -> int:
    """Fail runs left running by a restart and kill any child that is still alive."""
    init_optimization(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, pid FROM optimization_runs WHERE status = 'running'"
        ).fetchall()
        stuck = [(str(row["run_id"]), row["pid"]) for row in rows]
    for run_id, pid in stuck:
        _kill_pid(pid)
        _finish(db_path, run_id, status="failed", reason=_INTERRUPTED)
    return len(stuck)


def wait_for_run(run_id: str, timeout: float = 30) -> None:
    """Block until the background agent thread for this run has exited."""
    with _threads_lock:
        thread = _threads.get(run_id)
    if thread is None:
        return
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Run {run_id} is still running.")


def _start_thread(
    db_path: Path,
    workspace: Path,
    run_id: str,
    dev_path: Path,
    options: OptimizationSettings,
) -> None:
    interpreter = options.interpreter
    if interpreter is None:
        raise HermesUnavailable("Hermes is not set up on this server, so a run cannot start.")

    def _target() -> None:
        try:
            outcome = run_agent_job(
                workspace,
                run_id,
                prompt=optimization_prompt(run_id=run_id, workspace=workspace, dev_path=dev_path),
                model=options.model,
                base_url=options.base_url,
                api_key=options.api_key,
                interpreter=interpreter,
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
        finish_run(
            db_path,
            workspace,
            run_id,
            runner_error=outcome.error,
            timed_out=outcome.timed_out,
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


def _set_pid(db_path: Path, run_id: str, pid: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE optimization_runs SET pid = ? WHERE run_id = ? AND status = 'running'",
            (pid, run_id),
        )


def _kill_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


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
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE optimization_runs
            SET status = ?, reason = ?, primary_metric = ?, candidate_config_path = ?,
                mlflow_run_id = ?, summary = ?, ended_at = ?, pid = NULL
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
                run_id,
            ),
        )


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
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

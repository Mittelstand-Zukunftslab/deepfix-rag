"""Optimization runs owned by the API.

The same function starts a scheduled run and a manual one. It exports a feedback
snapshot, skips when too little is labeled, and otherwise launches one OpenHands child.
When the child names a candidate, API code scores it and may promote it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from deepfix_rag.agent_process import (
    AGENT_UNAVAILABLE_MESSAGE,
    AgentUnavailable,
    interpreter_imports_agent,
    missing_skills_reason,
    run_agent_job,
)
from deepfix_rag.config.settings import LOGGER, Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import export_feedback
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.promotion import PromotionDecision, apply_candidate
from deepfix_rag.storage import connect

RunStatus = Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
RunTrigger = Literal["cron", "manual"]
INTERRUPTED_REASON = "Interrupted: backend restarted while the run was in progress"
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
    # None uses the tree shipped beside the package (`.agents/skills`).
    skills_dir: Path | None = None


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
        skills_dir=settings.DEEPFIX_SKILLS_DIR,
    )


# Relative to the repository root, and to `/app` in the API image. The package is
# installed editable from `<root>/src`, so `skill_path()` is this path under
# `Path(__file__).parents[2]`.
SKILL_RELATIVE = Path(".agents") / "skills" / "optimization-loop" / "SKILL.md"


def skill_path() -> Path:
    """The optimization-loop skill shipped beside this package.

    An editable install lives at ``<root>/src/deepfix_rag``. This is
    ``<root>/.agents/skills/optimization-loop/SKILL.md``. The API image copies
    ``.agents`` to ``/app/.agents``, which is that path in the container.
    """
    return Path(__file__).resolve().parents[2] / SKILL_RELATIVE


def skills_dir() -> Path:
    """Directory that holds the optimization-loop skill and the pathology skills."""
    return skill_path().parent.parent


def resolved_skills_dir(override: Path | None = None) -> Path:
    """Skills directory for one run. ``override`` wins over the shipped tree."""
    return skills_dir() if override is None else override


def resolved_skill_path(override: Path | None = None) -> Path:
    """Optimization-loop ``SKILL.md`` for the same directory ``resolved_skills_dir`` uses."""
    return resolved_skills_dir(override) / "optimization-loop" / "SKILL.md"


def skills_missing_reason(override: Path | None = None) -> str | None:
    """None when the optimization-loop skill and its directory can be read."""
    directory = resolved_skills_dir(override)
    return missing_skills_reason(resolved_skill_path(override), directory)


def optimization_prompt(
    *,
    run_id: str,
    workspace: Path,
    dev_path: Path,
    min_primary_delta: float,
    skills: Path | None = None,
) -> str:
    """Instructions for one run. Holdout is never mentioned."""
    run_dir = workspace / "runs" / run_id
    directory = resolved_skills_dir(skills)
    skill = resolved_skill_path(skills)
    return (
        f"Follow the procedure in {skill} by running deepfix with --json. "
        f"The pathology skills it routes to are in the same directory tree: {directory}. "
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
    """Export feedback, then skip, refuse, or start one background agent run.

    A run that would start without a readable optimization-loop skill is stored
    as failed and does not launch the agent.
    """
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
        skills_reason = None if reason is not None else skills_missing_reason(options.skills_dir)
        if reason is None and skills_reason is None and not _can_launch(options):
            raise AgentUnavailable(AGENT_UNAVAILABLE_MESSAGE)
        if reason is not None:
            status: RunStatus = "skipped"
            stored_reason = reason
        elif skills_reason is not None:
            status = "failed"
            stored_reason = skills_reason
        else:
            status = "running"
            stored_reason = None
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
                stored_reason,
                exported.snapshot_id,
                len(dev_ids),
                new_count,
                len(exported.holdout),
                json.dumps(dev_ids),
                started_at,
                started_at if status != "running" else None,
            ),
        )
    if status == "failed" and skills_reason is not None:
        LOGGER.warning("Optimization run %s marked failed: %s", run_id, skills_reason)
    if status == "running":
        _start_thread(db_path, workspace, run_id, exported.dev_path, options, coordinator)
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


def _start_thread(
    db_path: Path,
    workspace: Path,
    run_id: str,
    dev_path: Path,
    options: OptimizationSettings,
    coordinator: IndexCoordinator | None,
) -> None:
    interpreter = options.interpreter
    if interpreter is None:
        raise AgentUnavailable(AGENT_UNAVAILABLE_MESSAGE)
    directory = resolved_skills_dir(options.skills_dir)
    skill = resolved_skill_path(options.skills_dir)

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
                    skills=directory,
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
                skill_path=skill,
                skills_directory=directory,
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
    """True when this process can start the child that the run will use."""
    if not _interpreter_ready(options.interpreter):
        return False
    if not options.api_key or not options.base_url or not options.model:
        return False
    if options.runner_script is not None:
        return options.runner_script.is_file()
    assert options.interpreter is not None
    return interpreter_imports_agent(options.interpreter)


_import_cache: dict[str, bool] = {}


def agent_available(options: OptimizationSettings) -> bool:
    """True when OpenHands can be started. The import check is cached for this process."""
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

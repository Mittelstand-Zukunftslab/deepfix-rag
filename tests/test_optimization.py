"""Optimization runs skip, conflict, and finish without starting OpenHands."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

import mlflow
import pytest

from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import is_holdout, normalize_query, query_id_for, record_vote
from deepfix_rag.interactions import record_interaction
from deepfix_rag.optimization import (
    INTERRUPTED_REASON,
    STALE_GRACE_SECONDS,
    STALE_REASON,
    OptimizationSettings,
    RunInProgress,
    fail_interrupted,
    fail_stale_runs,
    get_run,
    init_optimization,
    optimization_prompt,
    skill_path,
    skills_dir,
    start_optimization_run,
    wait_for_run,
)
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.run_bundle import DirectoryObjectStore
from deepfix_rag.storage import connect
from tests.test_hermes_client import HermesApi

pytestmark = pytest.mark.integration

BUCKET = "deepfix-index"


def _hermes_options(
    tmp_path: Path,
    api: HermesApi,
    **overrides: object,
) -> OptimizationSettings:
    """Options pointed at one fake Hermes API, with the skip rules off."""
    values: dict[str, object] = {
        "min_dev_questions": 0,
        "min_new_dev_questions": 0,
        "min_holdout_questions": 0,
        "timeout_seconds": 30,
        "source_env": {
            "DEEPFIX_BUNDLE_ROOT": str(tmp_path / "objects"),
            "S3_URI": f"s3://{BUCKET}/dev",
        },
        "private_paths": (str(tmp_path / "private"),),
        "step_log_dir": tmp_path / "private" / "optimization-logs",
        "hermes_base_url": api.base_url,
        "hermes_api_key": api.api_key,
        "hermes_status_poll_seconds": 0.05,
    }
    values.update(overrides)
    return OptimizationSettings(**values)  # type: ignore[arg-type]


def _store(tmp_path: Path) -> DirectoryObjectStore:
    return DirectoryObjectStore(tmp_path / "objects")


def _ready_workspace(tmp_path: Path, corpus_text: str = "public statute\n") -> tuple[Path, Path]:
    """A workspace the run bundle can stage: workspace, dev corpus, and a serving index."""
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    prepare_workspace(workspace)
    (workspace / "corpus" / "note.txt").write_text(corpus_text, encoding="utf-8")
    table = workspace / "indexes" / "current" / "documents.lance"
    table.mkdir(parents=True)
    (table / "data.arrow").write_bytes(b"index-bytes")
    return workspace, db_path


def _publish_result(tmp_path: Path, deepfix_id: str, result: dict | str | None) -> None:
    """Write the run's ``output/result.json`` as Hermes would, or leave it absent."""
    if result is None:
        return
    body = result.encode() if isinstance(result, str) else json.dumps(result).encode()
    _store(tmp_path).put(BUCKET, f"optimization/runs/{deepfix_id}/output/result.json", body)


NULL_RESULT = {
    "candidate_config_path": None,
    "primary_metric": "ndcg",
    "summary": "No change.",
    "mlflow_run_id": None,
    "metrics": {"ndcg": 0.5, "recall": 0.8, "precision": 0.4},
}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _tracking(tmp_path: Path) -> None:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")


def _options(tmp_path: Path, **overrides: object) -> OptimizationSettings:
    values: dict[str, object] = {
        "min_dev_questions": 50,
        "min_new_dev_questions": 10,
        "min_holdout_questions": 15,
        "timeout_seconds": 30,
        "source_env": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "private_paths": (str(tmp_path / "private"),),
        # Unreachable by default: a test that starts a run points this at HermesApi.
        "hermes_base_url": "http://127.0.0.1:1",
        "hermes_api_key": "test-key",
    }
    values.update(overrides)
    return OptimizationSettings(**values)  # type: ignore[arg-type]


def _dev_query() -> str:
    query = "harbor budget"
    suffix = 0
    while is_holdout(normalize_query(query)):
        suffix += 1
        query = f"harbor budget {suffix}"
    return query


def _label(db_path: Path, query: str) -> str:
    user_id = "admin"
    stored = record_interaction(
        db_path,
        kind="retrieval",
        user_id=user_id,
        query=query,
        build_id="build",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-1", score=1.0, text="The harbor budget was approved.")
        ],
    )
    record_vote(
        db_path,
        user_id=user_id,
        interaction_id=stored.interaction_id,
        target="passage",
        action="up",
        rank=1,
    )
    return query_id_for(normalize_query(query))


@pytest.mark.unit
def test_prompt_hides_holdout_and_thresholds_match_the_defaults() -> None:
    prompt = optimization_prompt(
        run_id="run1",
        workspace=Path("/data/workspace"),
        dev_path=Path("/data/workspace/feedback/snap/feedback_dev.jsonl"),
        min_primary_delta=0.025,
    )
    assert "holdout" not in prompt.lower()
    assert "at least 0.025" in prompt
    stricter = optimization_prompt(
        run_id="run1",
        workspace=Path("/data/workspace"),
        dev_path=Path("/data/workspace/feedback/snap/feedback_dev.jsonl"),
        min_primary_delta=0.1,
    )
    assert "at least 0.1 " in stricter
    assert "deepfix" in prompt and "--json" in prompt
    assert "workspace" in prompt
    assert "--dataset /data/workspace/feedback/snap/feedback_dev.jsonl" in prompt
    assert "/data/workspace/runs/run1/candidate.yaml" in prompt
    assert "/data/workspace/runs/run1/idx" in prompt
    assert str(skills_dir()) in prompt
    assert skills_dir() == skill_path().parent.parent
    assert (skills_dir() / "rerank-calibration" / "SKILL.md").is_file()
    assert "candidate_config_path null" in prompt
    assert "one hypothesis" in prompt.lower()
    assert "dense" in prompt and "hybrid" in prompt and "bm25" in prompt
    assert "result.json" in prompt
    assert "complete only when" in prompt
    assert str(skill_path()) in prompt
    assert skill_path().is_file()
    assert Settings.model_fields["OPTIMIZATION_AGENT"].default == "hermes"
    assert Settings.model_fields["HERMES_BASE_URL"].default == "http://hermes:8642"
    assert Settings.model_fields["OPTIMIZATION_MIN_DEV_QUESTIONS"].default == 50
    assert Settings.model_fields["OPTIMIZATION_MIN_NEW_DEV_QUESTIONS"].default == 10
    assert Settings.model_fields["OPTIMIZATION_MIN_HOLDOUT_QUESTIONS"].default == 15
    assert Settings.model_fields["OPTIMIZATION_MIN_PRIMARY_DELTA"].default == 0.025
    assert Settings.model_fields["OPTIMIZATION_RUN_TIMEOUT_HOURS"].default == 3


def test_empty_feedback_is_skipped_without_starting_an_agent(
    tmp_path: Path, sample_corpus_path: Path
) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    workspace.mkdir()
    (workspace / "corpus").mkdir(parents=True)
    shutil.copy(sample_corpus_path, workspace / "corpus" / "sample_corpus.jsonl")
    with HermesApi() as api:
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(
                tmp_path,
                api,
                # The shipped thresholds are what make this run skip.
                min_dev_questions=50,
                min_new_dev_questions=10,
                min_holdout_questions=15,
            ),
        )
        # A skipped run never submits to Hermes. Only the readiness probe runs.
        assert [call.path for call in api.calls if call.method == "POST"] == []
    assert run.status == "skipped"
    assert run.reason == (
        "Skipped: 0 labeled questions in dev, 50 needed. "
        "0 new labeled questions in dev, 10 needed. "
        "0 labeled questions in holdout, 15 needed."
    )
    assert run.dev_count == 0
    assert run.holdout_count == 0
    assert not (workspace / "runs" / run.run_id).exists()
    assert (workspace / "corpus" / "sample_corpus.jsonl").is_file()


def test_a_second_start_conflicts_while_one_run_is_active(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, reason, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at
            )
            VALUES ('active', 'running', 'manual', NULL, 'snap', 0, 0, 0, '[]', 't')
            """
        )
    with pytest.raises(RunInProgress, match="already in progress"):
        start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_options(
                tmp_path, min_dev_questions=0, min_new_dev_questions=0, min_holdout_questions=0
            ),
        )
    assert get_run(db_path, "active").status == "running"


def test_new_dev_questions_are_counted_since_the_last_non_skipped_run(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    query_id = _label(db_path, _dev_query())
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at, ended_at, baseline_metrics
            )
            VALUES ('old', 'succeeded', 'cron', 'old-snap', 1, 1, 0, ?, 't0', 't1', ?)
            """,
            (json.dumps([query_id]), json.dumps({"ndcg": 0.5, "recall": 0.8})),
        )
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="cron",
        options=_options(
            tmp_path, min_dev_questions=1, min_new_dev_questions=1, min_holdout_questions=0
        ),
    )
    assert run.status == "skipped"
    assert run.new_dev_count == 0
    assert run.dev_count == 1
    assert "0 new labeled questions in dev, 1 needed" in (run.reason or "")


def test_null_candidate_succeeds_and_a_candidate_does_not_replace_the_champion(
    tmp_path: Path,
) -> None:
    _tracking(tmp_path)
    workspace, db_path = _ready_workspace(tmp_path)
    champion = workspace / "configs" / "champion.yaml"
    before = champion.read_text(encoding="utf-8")

    # A run that recommends nothing: succeeds, and leaves the champion alone.
    with HermesApi() as api:
        api.on_accept = lambda hermes_id, deepfix_id: (
            _publish_result(tmp_path, deepfix_id, NULL_RESULT)
            or api.runs[hermes_id].update({"status": "completed"})
        )
        run = start_optimization_run(
            db_path, workspace, trigger="manual", options=_hermes_options(tmp_path, api)
        )
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "succeeded"
    assert finished.reason == "No change."
    assert "holdout" not in (finished.reason or "")
    assert champion.read_text(encoding="utf-8") == before
    assert finished.candidate_config_path is None

    # A run whose candidate cannot be read is rejected, never promoted.
    unreadable = tmp_path / "not-yaml.yaml"
    unreadable.write_text("not: [\n", encoding="utf-8")
    candidate = {
        "candidate_config_path": str(unreadable),
        "primary_metric": "ndcg",
        "summary": "Raised top_k.",
        "mlflow_run_id": "ml-1",
    }
    with HermesApi() as api:
        api.on_accept = lambda hermes_id, deepfix_id: (
            _publish_result(tmp_path, deepfix_id, candidate)
            or api.runs[hermes_id].update({"status": "completed"})
        )
        again = start_optimization_run(
            db_path, workspace, trigger="manual", options=_hermes_options(tmp_path, api)
        )
        wait_for_run(again.run_id)
    stored = get_run(db_path, again.run_id)
    assert stored.status == "rejected"
    assert stored.reason == "The candidate config could not be read."
    assert stored.candidate_config_path is not None
    assert champion.read_text(encoding="utf-8") == before


BLOCKED_RESULT = {
    "candidate_config_path": None,
    "primary_metric": "ndcg",
    "summary": "baseline did not finish",
    "mlflow_run_id": None,
    "metrics": {},
}
BLOCKED_SMOKE = {
    "status": "error",
    "error": (
        "MLflow experiment 'dev_rag_experiment' does not exist. "
        "Refusing to auto-create per contract - check dev_rag_experiment."
    ),
}


def _accept_with(api: HermesApi, tmp_path: Path, result: object) -> None:
    """Make the fake Hermes complete the run with this result payload."""

    def publish(hermes_id: str, deepfix_id: str) -> None:
        _publish_result(tmp_path, deepfix_id, result)
        api.runs[hermes_id]["status"] = "completed"

    api.on_accept = publish


def test_a_blocked_baseline_fails_with_the_smoke_error(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _ready_workspace(tmp_path)
    # The agent wrote state.json with status blocked and the eval error.
    with HermesApi() as api:
        _accept_with(api, tmp_path, BLOCKED_RESULT)
        run = start_optimization_run(
            db_path, workspace, trigger="manual", options=_hermes_options(tmp_path, api)
        )
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    # The reason quotes the agent's summary, so whatever it wrote about the
    # blocked baseline reaches the admin screen.
    assert finished.reason == "Blocked at baseline eval: baseline did not finish"
    assert finished.candidate_config_path is None


def test_a_null_candidate_without_metrics_is_not_a_completed_round(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _ready_workspace(tmp_path)
    empty = dict(BLOCKED_RESULT)
    with HermesApi() as api:
        _accept_with(api, tmp_path, empty)
        run = start_optimization_run(
            db_path, workspace, trigger="manual", options=_hermes_options(tmp_path, api)
        )
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "Blocked at baseline eval: baseline did not finish"


def test_a_succeeded_row_without_metrics_does_not_consume_dev_questions(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    query_id = _label(db_path, _dev_query())
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at, ended_at
            )
            VALUES ('blocked-baseline', 'succeeded', 'manual', 'snap', 1, 1, 0, ?, 't0', 't1')
            """,
            (json.dumps([query_id]),),
        )
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="cron",
        options=_options(
            tmp_path, min_dev_questions=1, min_new_dev_questions=2, min_holdout_questions=0
        ),
    )
    assert run.status == "skipped"
    assert run.new_dev_count == 1
    assert "1 new labeled questions in dev, 2 needed" in (run.reason or "")
    assert "0 new labeled questions" not in (run.reason or "")


def test_invalid_result_fails_with_the_runner_error(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _ready_workspace(tmp_path)
    with HermesApi() as api:
        # A result file that is not JSON is not a valid result.
        _accept_with(api, tmp_path, "{")
        run = start_optimization_run(
            db_path, workspace, trigger="manual", options=_hermes_options(tmp_path, api)
        )
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "The run did not write a valid result."
    assert "holdout" not in (finished.reason or "")


def test_a_run_that_exceeds_the_timeout_is_failed_and_stopped(tmp_path: Path) -> None:
    """A Hermes run that never finishes is stopped at the deadline, not left running."""
    _tracking(tmp_path)
    workspace, db_path = _ready_workspace(tmp_path)
    with HermesApi() as api:
        api.on_accept = lambda hermes_id, _deepfix_id: api.runs[hermes_id].update(
            {"status": "running"}
        )
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(
                tmp_path,
                api,
                timeout_seconds=1,
                heartbeat_stall_seconds=600,
                # The stop handshake is bounded; keep it short in a test.
                hermes_stop_wait_seconds=0.2,
            ),
        )
        wait_for_run(run.run_id, timeout=20)
        stop_calls = [call for call in api.calls if call.path.endswith("/stop")]
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "Timed out"
    # The API stops a run it gave up on, so Hermes does not keep working.
    assert stop_calls, [call.path for call in api.calls]


def _insert_run(
    db_path: Path,
    run_id: str,
    *,
    status: str = "running",
    started_at: str = "t",
    ended_at: str | None = None,
    pid: int | None = None,
    dev_query_ids: list[str] | None = None,
    trigger: str = "manual",
) -> None:
    init_optimization(db_path)
    ids = dev_query_ids or []
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, pid, started_at, ended_at
            )
            VALUES (?, ?, ?, 'snap', ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                run_id,
                status,
                trigger,
                len(ids),
                len(ids),
                json.dumps(ids),
                pid,
                started_at,
                ended_at,
            ),
        )


def _stored_pid(db_path: Path, run_id: str) -> int | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT pid FROM optimization_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()["pid"]


def test_restart_fails_a_running_run_and_kills_only_its_process(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "product.db"
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        _insert_run(db_path, "stuck", pid=process.pid, trigger="cron")
        _insert_run(
            db_path,
            "done",
            status="succeeded",
            pid=unrelated.pid,
            started_at="t0",
            ended_at="t1",
        )
        assert fail_interrupted(db_path) == 1
        finished = get_run(db_path, "stuck")
        assert finished.status == "failed"
        assert finished.reason == INTERRUPTED_REASON
        assert finished.ended_at is not None
        assert _stored_pid(db_path, "stuck") is None
        assert get_run(db_path, "done").status == "succeeded"
        assert _stored_pid(db_path, "done") == unrelated.pid
        process.wait(timeout=2)
        assert process.returncode is not None
        assert _alive(unrelated.pid)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if unrelated.poll() is None:
            unrelated.kill()
            unrelated.wait(timeout=2)


def test_restart_records_failure_when_the_pid_cannot_be_signaled(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "product.db"
    _insert_run(
        db_path,
        "orphan",
        pid=-1,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    assert fail_interrupted(db_path) == 1
    finished = get_run(db_path, "orphan")
    assert finished.status == "failed"
    assert finished.reason == INTERRUPTED_REASON
    assert finished.ended_at is not None
    assert _stored_pid(db_path, "orphan") is None
    assert _alive(os.getpid())


def test_a_running_run_fails_only_after_the_timeout_plus_grace(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "product.db"
    start = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    _insert_run(db_path, "old", started_at=start.isoformat(), dev_query_ids=["q-old"])
    _insert_run(db_path, "unparsed", started_at="t")
    timeout = 3 * 60 * 60
    limit = timeout + STALE_GRACE_SECONDS
    assert (
        fail_stale_runs(
            db_path,
            timeout_seconds=timeout,
            now=start + timedelta(seconds=timeout + 1),
        )
        == 0
    )
    assert get_run(db_path, "old").status == "running"
    assert (
        fail_stale_runs(db_path, timeout_seconds=timeout, now=start + timedelta(seconds=limit))
        == 0
    )
    assert (
        fail_stale_runs(
            db_path,
            timeout_seconds=timeout,
            now=start + timedelta(seconds=limit + 1),
        )
        == 1
    )
    stale = get_run(db_path, "old")
    assert stale.status == "failed"
    assert stale.reason == STALE_REASON
    assert stale.ended_at is not None
    assert _stored_pid(db_path, "old") is None
    assert get_run(db_path, "unparsed").status == "running"


def test_the_next_start_is_not_blocked_by_a_stale_run(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    # Test options time out in 30s, so this row is past that timeout plus the grace.
    old = datetime.now(timezone.utc) - timedelta(seconds=STALE_GRACE_SECONDS + 120)
    _insert_run(db_path, "old", started_at=old.isoformat(), dev_query_ids=["q-old"])
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="cron",
        options=_options(tmp_path),
    )
    assert run.status == "skipped"
    assert run.run_id != "old"
    stored = get_run(db_path, "old")
    assert stored.status == "failed"
    assert stored.reason == STALE_REASON
    assert stored.ended_at is not None


def test_a_run_inside_the_timeout_still_conflicts(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    _insert_run(db_path, "active", started_at=started.isoformat())
    with pytest.raises(RunInProgress, match="already in progress"):
        start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_options(
                tmp_path, min_dev_questions=0, min_new_dev_questions=0, min_holdout_questions=0
            ),
        )
    assert get_run(db_path, "active").status == "running"
    assert get_run(db_path, "active").ended_at is None


def test_a_failed_run_does_not_consume_new_dev_questions(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    query_id = _label(db_path, _dev_query())
    _insert_run(db_path, "orphan", dev_query_ids=[query_id], pid=-1)
    assert fail_interrupted(db_path) == 1
    assert get_run(db_path, "orphan").status == "failed"
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="cron",
        options=_options(
            tmp_path, min_dev_questions=1, min_new_dev_questions=2, min_holdout_questions=0
        ),
    )
    assert run.status == "skipped"
    assert run.new_dev_count == 1
    assert run.dev_count == 1
    assert "1 new labeled questions in dev, 2 needed" in (run.reason or "")
    assert "0 new labeled questions" not in (run.reason or "")

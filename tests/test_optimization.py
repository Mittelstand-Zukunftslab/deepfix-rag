"""Optimization runs skip, conflict, and finish without starting Hermes."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import mlflow
import pytest

from deepfix_rag.accounts import connect, create_admin
from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import is_holdout, normalize_query, query_id_for, record_vote
from deepfix_rag.interactions import record_interaction
from deepfix_rag.optimization import (
    OptimizationSettings,
    RunInProgress,
    fail_interrupted,
    get_run,
    init_optimization,
    optimization_prompt,
    skill_path,
    start_optimization_run,
    wait_for_run,
)
from deepfix_rag.pipeline import RetrievalResult

pytestmark = pytest.mark.integration

_SCRIPT = """
import json, os, subprocess, sys, time
from pathlib import Path
run_id = sys.argv[1]
out = Path("runs") / run_id
mode = Path("mode.txt").read_text(encoding="utf-8").strip()
if mode == "sleep":
    child = subprocess.Popen(["sleep", "60"])
    (out / "child.pid").write_text(str(child.pid), encoding="utf-8")
    (out / "parent.pid").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(60)
elif mode == "null":
    (out / "result.json").write_text(json.dumps({
        "candidate_config_path": None,
        "primary_metric": "ndcg",
        "summary": "No change.",
        "mlflow_run_id": None,
    }), encoding="utf-8")
elif mode == "candidate":
    target = out / "candidate.yaml"
    target.write_text("not: [\\n", encoding="utf-8")
    (out / "result.json").write_text(json.dumps({
        "candidate_config_path": str(target),
        "primary_metric": "ndcg",
        "summary": "Raised top_k.",
        "mlflow_run_id": "ml-1",
    }), encoding="utf-8")
elif mode == "bad":
    (out / "result.json").write_text("{", encoding="utf-8")
(out / "agent_output.json").write_text(
    json.dumps({"final_response": "the holdout set says promote", "error": None}),
    encoding="utf-8",
)
"""


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
        "interpreter": Path(sys.executable),
        "model": "openai/gpt-6-luna-pro",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
        "source_env": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "private_paths": (str(tmp_path / "private"),),
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
    user = create_admin(db_path, "admin@example.com", "correct-horse")
    stored = record_interaction(
        db_path,
        kind="retrieval",
        user_id=user.id,
        query=query,
        build_id="build",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-1", score=1.0, text="The harbor budget was approved.")
        ],
    )
    record_vote(
        db_path,
        user_id=user.id,
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
    )
    assert "holdout" not in prompt.lower()
    assert "deepfix" in prompt and "--json" in prompt
    assert "dense" in prompt and "hybrid" in prompt and "bm25" in prompt
    assert "result.json" in prompt
    assert str(skill_path()) in prompt
    assert skill_path().is_file()
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
    script = tmp_path / "runner.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    (workspace / "mode.txt").write_text("null\n", encoding="utf-8")
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="manual",
        options=_options(tmp_path, runner_script=script),
    )
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
                holdout_count, dev_query_ids, started_at, ended_at
            )
            VALUES ('old', 'succeeded', 'cron', 'old-snap', 1, 1, 0, ?, 't0', 't1')
            """,
            (json.dumps([query_id]),),
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
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    script = tmp_path / "runner.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    (workspace / "mode.txt").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "mode.txt").write_text("null\n", encoding="utf-8")
    options = _options(
        tmp_path,
        min_dev_questions=0,
        min_new_dev_questions=0,
        min_holdout_questions=0,
        runner_script=script,
    )
    skipped = start_optimization_run(db_path, workspace, trigger="manual", options=options)
    wait_for_run(skipped.run_id)
    # The directory is created by prepare_workspace before the mode file is required.
    champion = workspace / "configs" / "champion.yaml"
    before = champion.read_text(encoding="utf-8")
    finished = get_run(db_path, skipped.run_id)
    assert finished.status == "succeeded"
    assert finished.reason == "No change."
    assert "holdout" not in (finished.reason or "")
    job = json.loads(
        (workspace / "runs" / skipped.run_id / "job.json").read_text(encoding="utf-8")
    )
    assert "holdout" not in job["prompt"].lower()
    assert champion.read_text(encoding="utf-8") == before

    (workspace / "mode.txt").write_text("candidate\n", encoding="utf-8")
    again = start_optimization_run(db_path, workspace, trigger="manual", options=options)
    wait_for_run(again.run_id)
    stored = get_run(db_path, again.run_id)
    assert stored.status == "rejected"
    assert stored.reason == "The candidate config could not be read."
    assert stored.candidate_config_path is not None
    assert champion.read_text(encoding="utf-8") == before


def test_invalid_result_fails_with_the_runner_error(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    script = tmp_path / "runner.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    workspace.mkdir()
    (workspace / "mode.txt").write_text("bad\n", encoding="utf-8")
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="manual",
        options=_options(
            tmp_path,
            min_dev_questions=0,
            min_new_dev_questions=0,
            min_holdout_questions=0,
            runner_script=script,
        ),
    )
    wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "The run did not write a valid result."
    assert "holdout" not in (finished.reason or "")


def test_timeout_kills_the_agent_process_group(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    script = tmp_path / "runner.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    workspace.mkdir()
    (workspace / "mode.txt").write_text("sleep\n", encoding="utf-8")
    run = start_optimization_run(
        db_path,
        workspace,
        trigger="manual",
        options=_options(
            tmp_path,
            min_dev_questions=0,
            min_new_dev_questions=0,
            min_holdout_questions=0,
            timeout_seconds=2,
            runner_script=script,
        ),
    )
    wait_for_run(run.run_id, timeout=10)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "Timed out"
    run_dir = workspace / "runs" / run.run_id
    parent = int((run_dir / "parent.pid").read_text(encoding="utf-8"))
    child = int((run_dir / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (_alive(parent) or _alive(child)):
        time.sleep(0.05)
    assert _alive(parent) is False
    assert _alive(child) is False


def test_restart_fails_a_running_run_and_kills_its_process(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "product.db"
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, pid, started_at
            )
            VALUES ('stuck', 'running', 'cron', 'snap', 0, 0, 0, '[]', ?, 't')
            """,
            (process.pid,),
        )
    assert fail_interrupted(db_path) == 1
    finished = get_run(db_path, "stuck")
    assert finished.status == "failed"
    assert finished.reason == "Interrupted by restart"
    process.wait(timeout=2)
    assert process.returncode is not None

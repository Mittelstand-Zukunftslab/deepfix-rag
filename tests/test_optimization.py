"""Optimization runs skip, conflict, and finish without starting OpenHands."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import mlflow
import pytest

from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import is_holdout, normalize_query, query_id_for, record_vote
from deepfix_rag.interactions import record_interaction
from deepfix_rag.optimization import (
    INTERRUPTED_REASON,
    SKILL_RELATIVE,
    STALE_GRACE_SECONDS,
    STALE_REASON,
    OptimizationSettings,
    RunInProgress,
    fail_interrupted,
    fail_stale_runs,
    get_run,
    init_optimization,
    optimization_prompt,
    settings_from,
    skill_path,
    skills_dir,
    skills_missing_reason,
    start_optimization_run,
    wait_for_run,
)
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect

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
    assert Settings.model_fields["OPTIMIZATION_MIN_DEV_QUESTIONS"].default == 50
    assert Settings.model_fields["OPTIMIZATION_MIN_NEW_DEV_QUESTIONS"].default == 10
    assert Settings.model_fields["OPTIMIZATION_MIN_HOLDOUT_QUESTIONS"].default == 15
    assert Settings.model_fields["OPTIMIZATION_MIN_PRIMARY_DELTA"].default == 0.025
    assert Settings.model_fields["OPTIMIZATION_RUN_TIMEOUT_HOURS"].default == 3
    assert Settings.model_fields["OPTIMIZATION_GOAL_ROUNDS"].default == 3


@pytest.mark.unit
def test_prompt_uses_a_configured_skills_directory() -> None:
    directory = Path("/opt/skills")
    prompt = optimization_prompt(
        run_id="run1",
        workspace=Path("/data/workspace"),
        dev_path=Path("/data/workspace/feedback/snap/feedback_dev.jsonl"),
        min_primary_delta=0.025,
        skills=directory,
    )
    skill = directory / "optimization-loop" / "SKILL.md"
    assert str(skill) in prompt
    assert str(directory) in prompt


@pytest.mark.unit
def test_shipped_skills_are_readable() -> None:
    assert skills_missing_reason() is None
    assert skill_path().is_file()
    assert skills_dir().is_dir()
    assert (skills_dir() / "rerank-calibration" / "SKILL.md").is_file()


def test_missing_skills_name_the_skill_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    skill = missing / "optimization-loop" / "SKILL.md"
    assert skills_missing_reason(missing) == f"Agent skills missing: {skill}"
    present = tmp_path / "skills"
    present.mkdir()
    expected = present / "optimization-loop" / "SKILL.md"
    assert skills_missing_reason(present) == f"Agent skills missing: {expected}"


def test_unreadable_skill_file_and_directory_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "optimization-loop" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("procedure\n", encoding="utf-8")
    skill.chmod(0)
    try:
        assert skills_missing_reason(root) == f"Agent skills missing: {skill}"
    finally:
        skill.chmod(0o644)
    root.chmod(0o111)
    try:
        assert skills_missing_reason(root) == f"Agent skills missing: {root}"
    finally:
        root.chmod(0o755)


@pytest.mark.unit
def test_settings_forward_a_skills_directory() -> None:
    custom = Path("/opt/skills")
    options = settings_from(Settings(DEEPFIX_SKILLS_DIR=custom))
    assert options.skills_dir == custom


@pytest.mark.unit
def test_image_copies_skills_to_the_path_the_prompt_uses() -> None:
    repo = Path(__file__).resolve().parents[1]
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY src ./src" in dockerfile
    assert "uv pip install --no-deps -e ." in dockerfile
    installed = Path("/app/src/deepfix_rag/optimization.py")
    assert installed.parents[2] == Path("/app")
    image_skill = installed.parents[2] / SKILL_RELATIVE
    assert image_skill == Path("/app/.agents/skills/optimization-loop/SKILL.md")
    copied = _agents_copy_destination(dockerfile)
    assert copied / "skills" / "optimization-loop" / "SKILL.md" == image_skill
    assert skill_path() == (repo / SKILL_RELATIVE).resolve()
    assert skill_path().relative_to(repo) == SKILL_RELATIVE
    assert image_skill.relative_to("/app") == SKILL_RELATIVE
    shipped = sorted(path.parent.name for path in (repo / ".agents" / "skills").glob("*/SKILL.md"))
    assert "optimization-loop" in shipped
    assert {"chunking-tuning", "rerank-calibration", "hybrid-fusion-tuning"} <= set(shipped)
    ignore = (repo / ".dockerignore").read_text(encoding="utf-8")
    for name in shipped:
        relative = f".agents/skills/{name}/SKILL.md"
        assert not _dockerignore_excludes(ignore, relative), relative
    assert _dockerignore_excludes(ignore, ".git/HEAD")
    assert _dockerignore_excludes(ignore, "src/pkg/__pycache__/mod.pyc")
    assert _dockerignore_excludes(ignore, "src/mod.pyc")
    assert _dockerignore_excludes(ignore, "trace.log")
    assert _dockerignore_excludes(ignore, ".env")
    assert _dockerignore_excludes(ignore, ".env.local")
    assert _dockerignore_excludes(ignore, "web/node_modules/left-pad/index.js")
    assert not _dockerignore_excludes(ignore, "src/deepfix_rag/optimization.py")
    prompt = optimization_prompt(
        run_id="run1",
        workspace=Path("/data/workspace"),
        dev_path=Path("/data/workspace/feedback/snap/feedback_dev.jsonl"),
        min_primary_delta=0.025,
    )
    assert str(skill_path()) in prompt
    assert str(skills_dir()) in prompt


def _agents_copy_destination(dockerfile: str) -> Path:
    """Directory the Dockerfile's ``COPY .agents`` instruction creates."""
    workdir = Path("/")
    found: Path | None = None
    for raw in dockerfile.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "WORKDIR":
            candidate = Path(parts[1])
            workdir = candidate if candidate.is_absolute() else workdir / candidate
            continue
        if parts[0] != "COPY":
            continue
        args = [part for part in parts[1:] if not part.startswith("--")]
        if len(args) != 2:
            continue
        src, dest = args
        if src.rstrip("/") not in {".agents", "./.agents"}:
            continue
        destination = Path(dest)
        if not destination.is_absolute():
            destination = Path(os.path.normpath(workdir / destination))
        found = destination
    if found is None:
        raise AssertionError("Dockerfile does not copy .agents")
    return found


def _dockerignore_excludes(text: str, relative: str) -> bool:
    excluded = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        if _dockerignore_pattern_matches(pattern, relative):
            excluded = not negate
    return excluded


def _dockerignore_pattern_matches(pattern: str, relative: str) -> bool:
    token = pattern[1:] if pattern.startswith("/") else pattern
    token = token.rstrip("/")
    if not token:
        return False
    expression = _docker_glob_to_regex(token)
    if "/" not in token:
        return any(re.fullmatch(expression, part) for part in relative.split("/"))
    candidate = relative
    while True:
        if re.fullmatch(expression, candidate):
            return True
        if "/" not in candidate:
            return False
        candidate = candidate.rsplit("/", 1)[0]


def _docker_glob_to_regex(token: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(token):
        if token.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if token.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        char = token[index]
        if char == "*":
            parts.append("[^/]*")
            index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        if char == "[":
            end = token.find("]", index + 1)
            if end == -1:
                parts.append(re.escape(char))
                index += 1
                continue
            parts.append(token[index : end + 1])
            index = end + 1
            continue
        parts.append(re.escape(char))
        index += 1
    return "".join(parts)


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


def test_missing_skills_fail_the_run_without_starting_the_agent(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    marker = tmp_path / "agent-started"
    script = tmp_path / "runner.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n",
        encoding="utf-8",
    )
    missing = tmp_path / "no-skills"
    skill = missing / "optimization-loop" / "SKILL.md"
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
            skills_dir=missing,
        ),
    )
    wait_for_run(run.run_id, timeout=5)
    assert run.status == "failed"
    assert run.reason == f"Agent skills missing: {skill}"
    assert run.ended_at is not None
    stored = get_run(db_path, run.run_id)
    assert stored.status == "failed"
    assert stored.reason == run.reason
    assert not marker.exists()
    assert not (workspace / "runs" / run.run_id).exists()


def test_a_readable_skills_directory_is_the_path_in_the_prompt(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    script = tmp_path / "runner.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    workspace.mkdir()
    (workspace / "mode.txt").write_text("null\n", encoding="utf-8")
    skills = tmp_path / "skills"
    skill = skills / "optimization-loop" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("procedure\n", encoding="utf-8")
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
            skills_dir=skills,
        ),
    )
    wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "succeeded"
    job = json.loads((workspace / "runs" / run.run_id / "job.json").read_text(encoding="utf-8"))
    assert str(skill) in job["prompt"]
    assert str(skills) in job["prompt"]
    assert job["skill_path"] == str(skill)
    assert job["skills_dir"] == str(skills)


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


def test_an_interrupted_run_still_consumes_its_dev_questions(tmp_path: Path) -> None:
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
            tmp_path, min_dev_questions=1, min_new_dev_questions=1, min_holdout_questions=0
        ),
    )
    assert run.status == "skipped"
    assert run.new_dev_count == 0
    assert run.dev_count == 1
    assert "0 new labeled questions in dev, 1 needed" in (run.reason or "")

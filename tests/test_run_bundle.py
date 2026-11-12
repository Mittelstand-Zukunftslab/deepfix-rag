"""Run-bundle layout, holdout exclusion, and the fixed sync command."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from deepfix_rag.config.schema import DataSourceType, RAGConfig
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.hermes_iam import (
    HERMES_IAM_USER,
    admin_call_plan,
    authorize,
    hermes_prefix_policy,
)
from deepfix_rag.hermes_sync import download_input, main, upload_output
from deepfix_rag.index_manifest import write_index_manifest
from deepfix_rag.promotion import apply_candidate
from deepfix_rag.run_bundle import (
    DirectoryObjectStore,
    RecordingObjectStore,
    RunBundleError,
    candidate_index_uri,
    clear_index_prefix,
    index_object_prefix,
    plan_run_bundle,
    reject_holdout_leaks,
    stage_run_bundle,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
BUCKET = "deepfix-index"
HOLDOUT_QUERY = "holdout-query-text-9f3a-do-not-upload"
HOLDOUT_ID = "fb_holdout9f3a"
DEV_QUERY = "dev-query-text-1c2b"
DEV_ID = "fb_dev1c2bxxxx"
RUN_A = "runa0001"
RUN_B = "runb0002"


def _dev_file(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "feedback_dev.jsonl"
    row = {"query_id": DEV_ID, "query": text or DEV_QUERY, "doc_ids": ["doc-public"]}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _workspace(tmp_path: Path, *, ledger: bool = True) -> Path:
    workspace = tmp_path / "workspace"
    prepare_workspace(workspace)
    (workspace / "corpus" / "statute.json").write_text(
        '{"text":"public statute"}', encoding="utf-8"
    )
    notes = workspace / "corpus" / "notes"
    notes.mkdir()
    (notes / "a.txt").write_text("corpus note", encoding="utf-8")
    index = workspace / "indexes" / "current"
    table = index / "documents.lance"
    table.mkdir()
    (table / "data.arrow").write_bytes(b"index-bytes")
    if ledger:
        (workspace / "runs" / "ledger.jsonl").write_text(
            '{"run_id":"earlier","decision":"rejected"}\n',
            encoding="utf-8",
        )
    holdout_dir = workspace / "feedback" / "snap"
    holdout_dir.mkdir(parents=True)
    (holdout_dir / "feedback_holdout.jsonl").write_text(
        json.dumps({"query_id": HOLDOUT_ID, "query": HOLDOUT_QUERY}) + "\n",
        encoding="utf-8",
    )
    return workspace


def _assert_absent(chunks: list[bytes], needles: list[str]) -> None:
    blob = b"\n".join(chunks)
    for needle in needles:
        assert needle.encode("utf-8") not in blob


def test_plan_writes_the_run_prefix_and_rewrites_champion_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    plan = plan_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        bucket=BUCKET,
    )
    keys = set(plan.keys)
    prefix = f"optimization/runs/{RUN_A}"
    assert plan.prefix == prefix
    assert f"{prefix}/input/champion.yaml" in keys
    assert f"{prefix}/input/feedback_dev.jsonl" in keys
    assert f"{prefix}/input/corpus/statute.json" in keys
    assert f"{prefix}/input/corpus/notes/a.txt" in keys
    assert f"{prefix}/input/ledger.jsonl" in keys
    assert f"{prefix}/input/champion-index/documents.lance/data.arrow" in keys
    assert not any("feedback_holdout" in key or "holdout" in key for key in keys)
    assert plan.candidate_index_uri == (f"s3://{BUCKET}/optimization/runs/{RUN_A}/candidate-index")
    assert plan.champion_index_dir.endswith(f"/runs/{RUN_A}/input/champion-index")
    assert plan.corpus_path.endswith(f"/runs/{RUN_A}/input/corpus")

    champion = next(obj.body for obj in plan.objects if obj.key.endswith("champion.yaml"))
    text = champion.decode("utf-8")
    assert str(workspace) not in text
    loaded = RAGConfig.from_yaml(text)
    assert loaded.data.source_type is DataSourceType.DIRECTORY
    assert loaded.data.path_or_repo == plan.corpus_path
    assert loaded.retrieval.lancedb_index_dir == plan.champion_index_dir
    assert "input/champion-index" in loaded.retrieval.lancedb_index_dir
    assert write_index_manifest(plan.candidate_index_uri, loaded) is None


def test_each_run_gets_its_own_index_directory(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    dev = _dev_file(tmp_path)
    first = plan_run_bundle(run_id=RUN_A, workspace=workspace, dev_path=dev, bucket=BUCKET)
    second = plan_run_bundle(run_id=RUN_B, workspace=workspace, dev_path=dev, bucket=BUCKET)
    assert first.prefix != second.prefix
    assert set(first.keys).isdisjoint(second.keys)
    assert first.champion_index_dir != second.champion_index_dir
    assert RUN_A not in second.champion_index_dir
    assert RUN_B not in first.candidate_index_uri
    assert first.candidate_index_uri != second.candidate_index_uri


def test_ledger_is_omitted_when_the_workspace_has_none(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, ledger=False)
    plan = plan_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        bucket=BUCKET,
    )
    assert not any(key.endswith("/ledger.jsonl") for key in plan.keys)


def test_serving_index_symlink_is_copied_under_the_run_prefix(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    build = workspace / "indexes" / "builds" / "build1"
    build.mkdir(parents=True)
    (build / "table.bin").write_bytes(b"lance-table")
    current = workspace / "indexes" / "current"
    shutil.rmtree(current)
    current.symlink_to(build, target_is_directory=True)
    plan = plan_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        bucket=BUCKET,
    )
    copied = next(obj for obj in plan.objects if obj.key.endswith("/table.bin"))
    assert copied.body == b"lance-table"
    assert copied.key.endswith(f"/runs/{RUN_A}/input/champion-index/table.bin")


def test_staged_objects_do_not_contain_holdout_text_or_ids(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = RecordingObjectStore(DirectoryObjectStore(tmp_path / "objects"))
    plan = stage_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        store=store,
        bucket=BUCKET,
        holdout_queries=(HOLDOUT_QUERY,),
        holdout_query_ids=(HOLDOUT_ID,),
    )
    listed = store.inner.list(BUCKET, plan.prefix + "/")
    assert set(listed) == set(plan.keys)
    bodies = [store.inner.get(BUCKET, key) for key in listed]
    _assert_absent(bodies, [HOLDOUT_QUERY, HOLDOUT_ID])
    assert all(key.startswith(f"optimization/runs/{RUN_A}/") for key in listed)
    assert [request.key for request in store.requests] == list(plan.keys)
    assert {request.operation for request in store.requests} == {"put"}


def test_stage_refuses_a_dev_file_that_contains_a_holdout_query(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    dev = _dev_file(tmp_path, text=HOLDOUT_QUERY)
    store = DirectoryObjectStore(tmp_path / "objects")
    with pytest.raises(RunBundleError, match="holdout"):
        stage_run_bundle(
            run_id=RUN_A,
            workspace=workspace,
            dev_path=dev,
            store=store,
            bucket=BUCKET,
            holdout_queries=(HOLDOUT_QUERY,),
            holdout_query_ids=(HOLDOUT_ID,),
        )
    assert store.list(BUCKET, "optimization/runs/") == []


def test_sync_helper_round_trip_records_the_request_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    inner = DirectoryObjectStore(tmp_path / "objects")
    plan = stage_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        store=inner,
        bucket=BUCKET,
        holdout_queries=(HOLDOUT_QUERY,),
        holdout_query_ids=(HOLDOUT_ID,),
    )
    work = tmp_path / "work"
    downloaded = download_input(inner, bucket=BUCKET, run_id=RUN_A, work_dir=work)
    assert (work / "input" / "champion.yaml").is_file()
    assert (work / "input" / "feedback_dev.jsonl").read_text(encoding="utf-8").startswith("{")
    assert (work / "input" / "corpus" / "notes" / "a.txt").read_text(encoding="utf-8") == (
        "corpus note"
    )
    assert (work / "input" / "ledger.jsonl").is_file()
    assert (work / "input" / "champion-index" / "documents.lance" / "data.arrow").read_bytes() == (
        b"index-bytes"
    )
    assert not (work / "feedback").exists()
    result = work / "result.json"
    result.write_text('{"candidate_config_path":"candidate.yaml"}\n', encoding="utf-8")
    candidate = work / "candidate.yaml"
    candidate.write_text("version: '1.0'\n", encoding="utf-8")
    uploaded = upload_output(
        inner,
        bucket=BUCKET,
        run_id=RUN_A,
        result_path=result,
        candidate_path=candidate,
    )
    assert inner.get(BUCKET, f"{plan.prefix}/output/result.json") == result.read_bytes()
    assert inner.get(BUCKET, f"{plan.prefix}/output/candidate.yaml") == candidate.read_bytes()
    operations = (*downloaded, *uploaded)
    assert [request.operation for request in downloaded][0] == "list"
    assert all(request.key.startswith(f"optimization/runs/{RUN_A}/") for request in operations)
    assert not any(HOLDOUT_ID in request.key for request in operations)


def test_deepfix_sync_command_uses_the_directory_layout(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    bundle_root = tmp_path / "objects"
    stage_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=_dev_file(tmp_path),
        store=DirectoryObjectStore(bundle_root),
        bucket=BUCKET,
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DEEPFIX_BUNDLE_ROOT": str(bundle_root),
        "S3_URI": f"s3://{BUCKET}/dev",
    }
    work_root = tmp_path / "container"
    download = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepfix_rag.hermes_sync",
            "download",
            "--run-id",
            RUN_A,
            "--work-root",
            str(work_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert download.returncode == 0, download.stderr
    assert f"candidate_index_uri={candidate_index_uri(BUCKET, RUN_A)}" in download.stdout
    assert HOLDOUT_QUERY not in download.stdout
    assert (work_root / "runs" / RUN_A / "input" / "champion.yaml").is_file()
    result = tmp_path / "result.json"
    result.write_text("{}\n", encoding="utf-8")
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("version: '1.0'\n", encoding="utf-8")
    upload = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepfix_rag.hermes_sync",
            "upload",
            "--run-id",
            RUN_A,
            "--result",
            str(result),
            "--candidate",
            str(candidate),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert upload.returncode == 0, upload.stderr
    stored = DirectoryObjectStore(bundle_root)
    assert stored.get(BUCKET, f"optimization/runs/{RUN_A}/output/result.json") == b"{}\n"
    assert main(["--help"]) == 0


def test_prefix_policy_matches_the_prd_and_denies_other_keys() -> None:
    policy = hermes_prefix_policy(BUCKET)
    assert policy == {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation"],
                "Resource": [f"arn:aws:s3:::{BUCKET}"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{BUCKET}"],
                "Condition": {"StringLike": {"s3:prefix": ["optimization/runs/*"]}},
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{BUCKET}/optimization/runs/*"],
            },
        ],
    }
    inside = f"arn:aws:s3:::{BUCKET}/optimization/runs/{RUN_A}/input/champion.yaml"
    for action in ("s3:GetObject", "s3:PutObject", "s3:DeleteObject"):
        assert authorize(policy, action=action, resource=inside) == "Allow"
    outside = (
        f"arn:aws:s3:::{BUCKET}/dev/champion.yaml",
        f"arn:aws:s3:::{BUCKET}/holdout/questions.jsonl",
        f"arn:aws:s3:::{BUCKET}/optimization/other.txt",
        f"arn:aws:s3:::{BUCKET}/optimization/runs-evil/file",
    )
    for resource in outside:
        for action in ("s3:GetObject", "s3:PutObject", "s3:DeleteObject"):
            assert authorize(policy, action=action, resource=resource) == "AccessDenied"
    bucket = f"arn:aws:s3:::{BUCKET}"
    assert (
        authorize(
            policy,
            action="s3:ListBucket",
            resource=bucket,
            prefix=f"optimization/runs/{RUN_A}/input/",
        )
        == "Allow"
    )
    assert (
        authorize(policy, action="s3:ListBucket", resource=bucket, prefix="dev/") == "AccessDenied"
    )
    assert (
        authorize(policy, action="s3:ListBucket", resource=bucket, prefix="holdout/")
        == "AccessDenied"
    )
    assert authorize(policy, action="s3:ListBucket", resource=bucket, prefix=None) == (
        "AccessDenied"
    )
    assert authorize(policy, action="s3:GetBucketLocation", resource=bucket) == "Allow"
    assert HERMES_IAM_USER != "RUSTFS_ACCESS_KEY"
    assert HERMES_IAM_USER != "S3_ACCESS_KEY_ID"


def test_iam_policy_is_attached_to_the_user_and_not_issued_with_sts() -> None:
    rendered = json.dumps(
        [
            {"method": call.method, "path": call.path, "query": call.query}
            for call in admin_call_plan()
        ]
    )
    assert "AssumeRole" not in rendered
    assert "hermes-optimizer" in rendered
    attach = admin_call_plan()[-1]
    query = dict(attach.query)
    assert query["isGroup"] == "false"
    assert query["userOrGroup"] == HERMES_IAM_USER
    assert query["policyName"] == "hermes-optimizer-prefix"
    assert "secret" not in rendered


def test_promotion_rebuilds_locally_and_ignores_the_remote_candidate_index() -> None:
    source = inspect.getsource(apply_candidate)
    assert "build_beside" in source
    assert 'workspace / "indexes" / "current"' in source
    assert "candidate-index" not in source
    assert "s3://" not in source
    script = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'deepfix-sync = "deepfix_rag.hermes_sync:main"' in script
    dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    assert "/usr/local/bin/deepfix-sync" in dockerfile


def test_force_clear_deletes_only_the_index_prefix(tmp_path: Path) -> None:
    store = DirectoryObjectStore(tmp_path / "objects")
    store.put(BUCKET, "dev/documents.lance/data.arrow", b"half")
    store.put(BUCKET, "dev/manifest.json", b"old")
    store.put(BUCKET, "device/keep.txt", b"sibling")
    store.put(BUCKET, "holdout/questions.jsonl", b"secret")
    store.put(BUCKET, "optimization/runs/runa0001/input/champion.yaml", b"run")
    removed = clear_index_prefix(store, f"s3://{BUCKET}/dev")
    assert removed == 2
    assert store.list(BUCKET, "dev/") == []
    assert store.get(BUCKET, "device/keep.txt") == b"sibling"
    assert store.get(BUCKET, "holdout/questions.jsonl") == b"secret"
    assert store.get(BUCKET, "optimization/runs/runa0001/input/champion.yaml") == b"run"
    bucket, prefix = index_object_prefix(
        f"s3://{BUCKET}/optimization/runs/runa0001/candidate-index"
    )
    assert bucket == BUCKET
    assert prefix == "optimization/runs/runa0001/candidate-index/"
    for uri in (f"s3://{BUCKET}", f"s3://{BUCKET}/", f"s3://{BUCKET}/../dev"):
        with pytest.raises(RunBundleError, match="empty prefix"):
            clear_index_prefix(store, uri)


def test_holdout_leak_names_the_dev_question_and_not_the_query(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    concatenated = f"{HOLDOUT_QUERY}hat is the rest of a second question"
    dev_path = _dev_file(tmp_path, concatenated)
    plan = plan_run_bundle(
        run_id=RUN_A,
        workspace=workspace,
        dev_path=dev_path,
        bucket=BUCKET,
    )
    with pytest.raises(RunBundleError, match=DEV_ID) as caught:
        reject_holdout_leaks(plan, queries=(HOLDOUT_QUERY,), query_ids=(HOLDOUT_ID,))
    message = str(caught.value)
    assert "dev question" in message
    assert HOLDOUT_QUERY not in message
    assert HOLDOUT_ID not in message


def test_run_id_with_a_slash_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(RunBundleError):
        plan_run_bundle(
            run_id="../other",
            workspace=workspace,
            dev_path=_dev_file(tmp_path),
            bucket=BUCKET,
        )

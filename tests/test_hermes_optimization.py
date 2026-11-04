"""Hermes optimization lifecycle against a local HTTP server and a directory store.

The OpenHands path is the default. These tests do not start the real agent and
do not mock evaluation or the pipeline. ``result.json`` is an object the test
server writes; the API downloader copies it into the workspace.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import mlflow
from pydantic import ValidationError
import pytest

from deepfix_rag.agent_process import AGENT_UNAVAILABLE_MESSAGE
from deepfix_rag.api.app import create_app
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import is_holdout, normalize_query, query_id_for, record_vote
from deepfix_rag.hermes_client import HERMES_MODEL, HERMES_PROVIDER
from deepfix_rag.hermes_sync import download_result
from deepfix_rag.interactions import record_interaction
from deepfix_rag.optimization import (
    HERMES_STATUS_MESSAGE,
    HERMES_STATUS_POLL_SECONDS,
    HERMES_UNAVAILABLE_MESSAGE,
    INTERRUPTED_REASON,
    OPENHANDS_STATUS_MESSAGE,
    OptimizationAgent,
    OptimizationSettings,
    agent_available,
    get_run,
    init_optimization,
    settings_from,
    start_optimization_run,
    wait_for_run,
)
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.run_bundle import DirectoryObjectStore
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_hermes_client import API_KEY, HermesApi

pytestmark = pytest.mark.integration

BUCKET = "deepfix-index"
_NULL_RESULT = {
    "candidate_config_path": None,
    "primary_metric": "ndcg",
    "summary": "No change.",
    "mlflow_run_id": None,
}
_OPENHANDS_RUNNER = """
import json, sys
from pathlib import Path
run_id = sys.argv[1]
out = Path("runs") / run_id
(out / "result.json").write_text(json.dumps({
    "candidate_config_path": None,
    "primary_metric": "ndcg",
    "summary": "No change.",
    "mlflow_run_id": None,
}), encoding="utf-8")
"""


def _tracking(tmp_path: Path) -> None:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")


def _holdout_query() -> str:
    query = "sealed holdout phrase"
    suffix = 0
    while not is_holdout(normalize_query(query)):
        suffix += 1
        query = f"sealed holdout phrase {suffix}"
    return normalize_query(query)


def _label(db_path: Path, query: str) -> str:
    stored = record_interaction(
        db_path,
        kind="retrieval",
        user_id="admin",
        query=query,
        build_id="build",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-1", score=1.0, text="The harbor budget was approved.")
        ],
    )
    record_vote(
        db_path,
        user_id="admin",
        interaction_id=stored.interaction_id,
        target="passage",
        action="up",
        rank=1,
    )
    return query_id_for(normalize_query(query))


def _workspace(tmp_path: Path, corpus_text: str) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    prepare_workspace(workspace)
    (workspace / "corpus" / "note.txt").write_text(corpus_text, encoding="utf-8")
    table = workspace / "indexes" / "current" / "documents.lance"
    table.mkdir(parents=True)
    (table / "data.arrow").write_bytes(b"index-bytes")
    return workspace, db_path


def _hermes_options(tmp_path: Path, api: HermesApi) -> OptimizationSettings:
    return OptimizationSettings(
        min_dev_questions=0,
        min_new_dev_questions=0,
        min_holdout_questions=0,
        timeout_seconds=5,
        interpreter=None,
        api_key=None,
        agent=OptimizationAgent.HERMES,
        hermes_base_url=api.base_url,
        hermes_api_key=api.api_key,
        hermes_status_poll_seconds=0.05,
        source_env={
            "DEEPFIX_BUNDLE_ROOT": str(tmp_path / "objects"),
            "S3_URI": f"s3://{BUCKET}/dev",
        },
        private_paths=(str(tmp_path / "private"),),
    )


def _store(tmp_path: Path) -> DirectoryObjectStore:
    return DirectoryObjectStore(tmp_path / "objects")


def _bodies(store: DirectoryObjectStore, run_id: str) -> list[bytes]:
    prefix = f"optimization/runs/{run_id}/"
    return [store.get(BUCKET, key) for key in store.list(BUCKET, prefix)]


@pytest.mark.unit
def test_the_flag_defaults_to_openhands_and_rejects_other_names() -> None:
    assert Settings.model_fields["OPTIMIZATION_AGENT"].default == "openhands"
    assert OptimizationSettings().agent is OptimizationAgent.OPENHANDS
    assert OptimizationSettings().hermes_status_poll_seconds == HERMES_STATUS_POLL_SECONDS
    copied = settings_from(
        Settings(
            OPTIMIZATION_AGENT="hermes",
            HERMES_BASE_URL="http://hermes:8642/",
            API_SERVER_KEY=API_KEY,
            OPENHANDS_PYTHON=None,
        ),
        source_env={},
    )
    assert copied.agent is OptimizationAgent.HERMES
    assert copied.hermes_base_url == "http://hermes:8642"
    assert copied.hermes_api_key == API_KEY
    plain = settings_from(Settings(OPENHANDS_PYTHON=None, API_SERVER_KEY=None), source_env={})
    assert plain.agent is OptimizationAgent.OPENHANDS
    with pytest.raises(ValidationError):
        Settings(OPTIMIZATION_AGENT="both")


@pytest.mark.unit
def test_hermes_is_available_only_when_health_succeeds_and_the_key_is_set() -> None:
    with HermesApi() as api:
        ready = OptimizationSettings(
            agent=OptimizationAgent.HERMES,
            hermes_base_url=api.base_url,
            hermes_api_key=api.api_key,
            interpreter=None,
        )
        assert agent_available(ready) is True
        health = [call for call in api.calls if call.path.split("?", 1)[0] == "/health"]
        assert len(health) == 1
        assert health[0].authorization_present is False
        assert health[0].authorized is False
        assert API_KEY.encode() not in health[0].body

        blank = OptimizationSettings(
            agent=OptimizationAgent.HERMES,
            hermes_base_url=api.base_url,
            hermes_api_key=None,
            interpreter=None,
        )
        before = len(api.calls)
        assert agent_available(blank) is False
        assert len(api.calls) == before

        api.health_ok = False
        assert agent_available(ready) is False

    refused = OptimizationSettings(
        agent=OptimizationAgent.HERMES,
        hermes_base_url="http://127.0.0.1:1",
        hermes_api_key=API_KEY,
        interpreter=None,
    )
    assert agent_available(refused) is False

    with HermesApi() as api:
        openhands = OptimizationSettings(
            agent=OptimizationAgent.OPENHANDS,
            interpreter=None,
            hermes_base_url=api.base_url,
            hermes_api_key=api.api_key,
        )
        assert agent_available(openhands) is False
        assert api.calls == []


@pytest.mark.unit
def test_download_result_writes_the_output_object_and_ignores_a_missing_one(
    tmp_path: Path,
) -> None:
    store = DirectoryObjectStore(tmp_path / "objects")
    destination = tmp_path / "runs" / "run-1" / "result.json"
    assert (
        download_result(
            store,
            bucket=BUCKET,
            run_id="run-1",
            destination=destination,
        )
        is False
    )
    assert destination.exists() is False
    store.put(BUCKET, "optimization/runs/run-1/output/result.json", b'{"ok": true}\n')
    assert download_result(store, bucket=BUCKET, run_id="run-1", destination=destination) is True
    assert destination.read_bytes() == b'{"ok": true}\n'


def test_unset_flag_keeps_the_openhands_child(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _tracking(tmp_path)
    caplog.set_level(logging.DEBUG)
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    script = tmp_path / "runner.py"
    script.write_text(_OPENHANDS_RUNNER, encoding="utf-8")
    with HermesApi() as api:
        options = OptimizationSettings(
            min_dev_questions=0,
            min_new_dev_questions=0,
            min_holdout_questions=0,
            timeout_seconds=30,
            interpreter=Path(sys.executable),
            model="openai/gpt-6-luna-pro",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
            runner_script=script,
            source_env={"PATH": "/usr/bin:/bin"},
            hermes_base_url=api.base_url,
            hermes_api_key=api.api_key,
        )
        assert options.agent is OptimizationAgent.OPENHANDS
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
        wait_for_run(run.run_id)
        assert api.calls == []
    finished = get_run(db_path, run.run_id)
    assert finished.status == "succeeded"
    assert finished.reason == "No change."
    job = workspace / "runs" / run.run_id / "job.json"
    assert job.is_file()
    assert "holdout" not in job.read_text(encoding="utf-8").lower()
    assert API_KEY not in caplog.text


def test_hermes_null_candidate_succeeds_from_the_downloaded_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _tracking(tmp_path)
    caplog.set_level(logging.DEBUG)
    query = _holdout_query()
    query_id = query_id_for(query)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    _label(db_path, query)
    champion = (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8")
    payload = json.dumps(_NULL_RESULT).encode()
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "completed"
            api.runs[hermes_id]["output"] = "the holdout set says promote this candidate"
            _store(tmp_path).put(
                BUCKET,
                f"optimization/runs/{deepfix_id}/output/result.json",
                payload,
            )

        api.on_accept = publish
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(tmp_path, api),
        )
        wait_for_run(run.run_id)
        calls = list(api.calls)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "succeeded"
    assert finished.reason == "No change."
    assert "holdout" not in (finished.reason or "").lower()
    local = workspace / "runs" / run.run_id / "result.json"
    assert local.read_bytes() == payload
    assert (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8") == champion
    assert not (workspace / "runs" / run.run_id / "job.json").exists()

    store = _store(tmp_path)
    listed = store.list(BUCKET, f"optimization/runs/{run.run_id}/")
    assert f"optimization/runs/{run.run_id}/output/result.json" in listed
    assert f"optimization/runs/{run.run_id}/input/feedback_dev.jsonl" in listed
    assert f"optimization/runs/{run.run_id}/input/champion.yaml" in listed
    blob = b"\n".join(_bodies(store, run.run_id))
    assert query.encode() not in blob
    assert query_id.encode() not in blob
    assert b"holdout" not in blob.lower()
    rewritten = store.get(BUCKET, f"optimization/runs/{run.run_id}/input/champion.yaml")
    loaded = RAGConfig.from_yaml(rewritten.decode())
    assert run.run_id in loaded.retrieval.lancedb_index_dir
    assert str(workspace) not in rewritten.decode()
    ledger = (workspace / "runs" / "ledger.jsonl").read_text(encoding="utf-8")
    assert query not in ledger
    assert "holdout" not in ledger.lower()

    posts = [call for call in calls if call.method == "POST" and call.path == "/v1/runs"]
    assert len(posts) == 1
    assert posts[0].headers.get("Idempotency-Key") == run.run_id
    body = json.loads(posts[0].body)
    assert set(body) == {"input", "instructions", "provider", "model"}
    assert body["input"] == body["instructions"]
    assert body["provider"] == HERMES_PROVIDER
    assert body["model"] == HERMES_MODEL
    instructions = body["instructions"]
    assert "candidate_config_path" in instructions
    assert "primary_metric" in instructions
    assert "summary" in instructions
    assert "mlflow_run_id" in instructions
    assert "feedback_dev.jsonl" in instructions
    assert "champion.yaml" in instructions
    assert f"/opt/data/workspace/runs/{run.run_id}" in instructions
    assert "holdout" not in instructions.lower()
    assert "product.db" not in instructions
    assert str(workspace) not in instructions
    assert str(tmp_path / "private") not in instructions
    assert API_KEY not in instructions
    gets = [
        call
        for call in calls
        if call.method == "GET"
        and call.path.startswith("/v1/runs/")
        and "/events" not in call.path
    ]
    assert gets
    assert all(call.authorized for call in posts + gets)
    assert not any(call.path.endswith("/stop") or call.path.endswith("/events") for call in calls)
    health = [call for call in calls if call.path.split("?", 1)[0] == "/health"]
    assert health
    assert all(call.authorization_present is False for call in health)
    assert API_KEY not in caplog.text
    assert API_KEY not in (finished.reason or "")


@pytest.mark.parametrize("payload", [b"{", None])
def test_invalid_or_missing_result_fails_without_promoting(
    tmp_path: Path, payload: bytes | None
) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    champion = (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8")
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "completed"
            api.runs[hermes_id]["output"] = "promote the holdout set"
            if payload is None:
                return
            _store(tmp_path).put(
                BUCKET,
                f"optimization/runs/{deepfix_id}/output/result.json",
                payload,
            )

        api.on_accept = publish
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(tmp_path, api),
        )
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "The run did not write a valid result."
    assert "holdout" not in (finished.reason or "").lower()
    assert (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8") == champion
    local = workspace / "runs" / run.run_id / "result.json"
    if payload is None:
        assert local.exists() is False
    else:
        assert local.read_bytes() == payload


def test_a_bundle_that_contains_holdout_text_is_not_submitted(tmp_path: Path) -> None:
    _tracking(tmp_path)
    query = _holdout_query()
    workspace, db_path = _workspace(tmp_path, f"notes\n{query}\n")
    query_id = _label(db_path, query)
    with HermesApi() as api:
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(tmp_path, api),
        )
        wait_for_run(run.run_id)
        calls = list(api.calls)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "refusing to upload a bundle that contains holdout content"
    assert query not in (finished.reason or "")
    assert query_id not in (finished.reason or "")
    assert _store(tmp_path).list(BUCKET, "") == []
    assert not any(call.path.startswith("/v1/runs") for call in calls)


def test_admin_status_text_follows_the_flag(tmp_path: Path, identity: IdentityIssuer) -> None:
    _tracking(tmp_path)
    common = {
        "PRODUCT_DATA_DIR": tmp_path / "private",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "WEB_ORIGIN": "http://localhost:8080",
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        "OPENHANDS_PYTHON": None,
        "OPTIMIZATION_MIN_DEV_QUESTIONS": 0,
        "OPTIMIZATION_MIN_NEW_DEV_QUESTIONS": 0,
        "OPTIMIZATION_MIN_HOLDOUT_QUESTIONS": 0,
    }
    open_settings = Settings(**common, OPTIMIZATION_AGENT="openhands", API_SERVER_KEY=None)
    with TestClient(create_app(open_settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["agent"] == "openhands"
        assert body["agent_available"] is False
        assert body["agent_unavailable_message"] == OPENHANDS_STATUS_MESSAGE
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 503
        assert started.json()["detail"] == AGENT_UNAVAILABLE_MESSAGE

    hermes_settings = Settings(
        **common,
        OPTIMIZATION_AGENT="hermes",
        HERMES_BASE_URL="http://127.0.0.1:1",
        API_SERVER_KEY=None,
    )
    with TestClient(create_app(hermes_settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["agent"] == "hermes"
        assert body["agent_available"] is False
        assert body["agent_unavailable_message"] == HERMES_STATUS_MESSAGE
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 503
        assert started.json()["detail"] == HERMES_UNAVAILABLE_MESSAGE

    with HermesApi() as api:
        live = Settings(
            **common,
            OPTIMIZATION_AGENT="hermes",
            HERMES_BASE_URL=api.base_url,
            API_SERVER_KEY=api.api_key,
        )
        with TestClient(create_app(live, token_verifier=identity.verifier())) as client:
            identity.sign_in(client, "admin@example.com", "admin")
            status = client.get("/admin/optimization")
            assert status.status_code == 200, status.text
            body = status.json()
            assert body["agent"] == "hermes"
            assert body["agent_available"] is True
            assert body["agent_unavailable_message"] is None
            assert API_KEY not in status.text


def test_startup_still_fails_a_running_row_when_the_flag_is_hermes(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    settings = Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
        OPTIMIZATION_AGENT="hermes",
        API_SERVER_KEY=None,
    )
    init_optimization(settings.product_db_path)
    with connect(settings.product_db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, pid, started_at
            )
            VALUES ('orphan', 'running', 'manual', 'snap', 0, 0, 0, '[]', -1, 't')
            """
        )
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        run = status.json()["runs"][0]
        assert run["run_id"] == "orphan"
        assert run["status"] == "failed"
        assert run["reason"] == INTERRUPTED_REASON

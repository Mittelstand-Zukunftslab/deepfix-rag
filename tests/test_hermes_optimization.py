"""Hermes optimization lifecycle against a local HTTP server and a directory store.

The OpenHands path is the default. These tests do not start the real agent and
do not mock evaluation or the pipeline. ``result.json`` is an object the test
server writes; the API downloader copies it into the workspace.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time

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
    CANCELLED_REASON,
    HERMES_INTERRUPTED_REASON,
    HERMES_STATUS_MESSAGE,
    HERMES_STATUS_POLL_SECONDS,
    HERMES_UNAVAILABLE_MESSAGE,
    INTERRUPTED_REASON,
    OPENHANDS_STATUS_MESSAGE,
    STALE_REASON,
    OptimizationAgent,
    OptimizationSettings,
    agent_available,
    get_run,
    init_optimization,
    settings_from,
    stall_reason,
    start_optimization_run,
    wait_for_run,
)
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.run_bundle import DirectoryObjectStore
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer
from tests.test_hermes_client import API_KEY, HermesApi, _frame

pytestmark = pytest.mark.integration

BUCKET = "deepfix-index"
_NULL_RESULT = {
    "candidate_config_path": None,
    "primary_metric": "ndcg",
    "summary": "No change.",
    "mlflow_run_id": None,
    "metrics": {"ndcg": 0.5, "recall": 0.8, "precision": 0.4},
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
    "metrics": {"ndcg": 0.5, "recall": 0.8, "precision": 0.4},
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
        step_log_dir=tmp_path / "private" / "optimization-logs",
    )


def _store(tmp_path: Path) -> DirectoryObjectStore:
    return DirectoryObjectStore(tmp_path / "objects")


def _bodies(store: DirectoryObjectStore, run_id: str) -> list[bytes]:
    prefix = f"optimization/runs/{run_id}/"
    return [store.get(BUCKET, key) for key in store.list(BUCKET, prefix)]


@pytest.mark.unit
def test_the_flag_defaults_to_openhands_and_rejects_other_names() -> None:
    assert Settings.model_fields["OPTIMIZATION_AGENT"].default == "openhands"
    assert Settings.model_fields["OPTIMIZATION_HEARTBEAT_STALL_MINUTES"].default == 5
    assert OptimizationSettings().heartbeat_stall_seconds == 5 * 60
    assert stall_reason(5 * 60) == "Stalled: no heartbeat for 5 minutes."
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
    assert finished.hermes_run_id is None
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
    assert finished.hermes_run_id
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
    events = [call for call in calls if call.path.endswith("/events")]
    assert events
    assert all(call.authorized for call in posts + gets + events)
    assert not any(call.path.endswith("/stop") for call in calls)
    step_log = tmp_path / "private" / "optimization-logs" / f"{run.run_id}.log"
    logged = step_log.read_text(encoding="utf-8")
    assert f"optimization run={run.run_id} hermes_run={finished.hermes_run_id} " in logged
    assert "event=tool.started tool=terminal preview=deepfix eval" in logged
    assert "event=keepalive" not in logged
    assert "event=running" not in logged
    assert str(tmp_path / "workspace") not in str(step_log)
    assert b"optimization-logs" not in blob
    health = [call for call in calls if call.path.split("?", 1)[0] == "/health"]
    assert health
    assert all(call.authorization_present is False for call in health)
    assert API_KEY not in caplog.text
    assert API_KEY not in (finished.reason or "")


def test_an_iteration_budget_wrapup_still_requires_result_json(tmp_path: Path) -> None:
    """max_iterations_reached is a wrap-up, not a cancel. The file still decides the row."""
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    _label(db_path, _holdout_query())
    payload = json.dumps(_NULL_RESULT).encode()
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "failed"
            api.runs[hermes_id]["completed"] = False
            api.runs[hermes_id]["turn_exit_reason"] = "max_iterations_reached(40/40)"
            api.runs[hermes_id]["output"] = "Iteration budget exhausted"
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
    assert finished.status == "succeeded"
    assert finished.reason == "No change."
    assert finished.reason != "Cancelled"
    assert (workspace / "runs" / run.run_id / "result.json").read_bytes() == payload


def test_an_iteration_budget_wrapup_without_result_json_fails(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    _label(db_path, _holdout_query())
    champion = (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8")
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            del deepfix_id
            api.runs[hermes_id]["status"] = "failed"
            api.runs[hermes_id]["turn_exit_reason"] = "max_iterations_reached(40/40)"

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
    assert finished.reason != "Cancelled"
    assert (workspace / "configs" / "champion.yaml").read_text(encoding="utf-8") == champion


def test_a_failed_hermes_run_does_not_read_result_json(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    _label(db_path, _holdout_query())
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "failed"
            api.runs[hermes_id]["error"] = "agent run failed"
            _store(tmp_path).put(
                BUCKET,
                f"optimization/runs/{deepfix_id}/output/result.json",
                json.dumps(_NULL_RESULT).encode(),
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
    assert finished.reason == "agent run failed"


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
    """A recorded pid and no Hermes id is an OpenHands child, even if the flag is hermes."""
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


def _wait_until(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met")


def _hermes_settings(tmp_path: Path, api: HermesApi, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "PRODUCT_DATA_DIR": tmp_path / "private",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "WEB_ORIGIN": "http://localhost:8080",
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        "OPENHANDS_PYTHON": None,
        "OPTIMIZATION_AGENT": "hermes",
        "HERMES_BASE_URL": api.base_url,
        "API_SERVER_KEY": api.api_key,
        "OPTIMIZATION_MIN_DEV_QUESTIONS": 1,
        "OPTIMIZATION_MIN_NEW_DEV_QUESTIONS": 1,
        "OPTIMIZATION_MIN_HOLDOUT_QUESTIONS": 1,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _insert_hermes_row(
    db_path: Path,
    run_id: str,
    *,
    hermes_run_id: str | None,
    started_at: str,
    pid: int | None = None,
) -> None:
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, pid, hermes_run_id, started_at
            )
            VALUES (?, 'running', 'manual', 'snap', 0, 0, 0, '[]', ?, ?, ?)
            """,
            (run_id, pid, hermes_run_id, started_at),
        )


def _bundle_env(tmp_path: Path) -> dict[str, str | None]:
    previous = {key: os.environ.get(key) for key in ("DEEPFIX_BUNDLE_ROOT", "S3_URI")}
    os.environ["DEEPFIX_BUNDLE_ROOT"] = str(tmp_path / "objects")
    os.environ["S3_URI"] = f"s3://{BUCKET}/dev"
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_keepalives_alone_stall_before_the_timeout(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    with HermesApi() as api:
        api.events_body = b": keepalive\n\n"
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(
            **{**options.__dict__, "heartbeat_stall_seconds": 0.6, "timeout_seconds": 30}
        )
        started = time.monotonic()
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
        wait_for_run(run.run_id, timeout=10)
        elapsed = time.monotonic() - started
        calls = list(api.calls)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == stall_reason(0.6)
    assert elapsed < 10
    assert any(call.path.endswith("/stop") for call in calls)
    log_path = tmp_path / "private" / "optimization-logs" / f"{run.run_id}.log"
    if log_path.is_file():
        assert "event=keepalive" not in log_path.read_text(encoding="utf-8")


def test_a_fresh_heartbeat_file_keeps_the_row_running(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    with HermesApi() as api:
        api.events_body = b": keepalive\n\n"

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "running"

        api.on_accept = publish
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(
            **{**options.__dict__, "heartbeat_stall_seconds": 0.6, "timeout_seconds": 30}
        )
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
        hermes_id = ""

        def stored() -> bool:
            nonlocal hermes_id
            hermes_id = get_run(db_path, run.run_id).hermes_run_id or ""
            return bool(hermes_id)

        _wait_until(stored)
        store = _store(tmp_path)
        key = f"optimization/runs/{run.run_id}/output/heartbeat.json"
        deadline = time.monotonic() + 1.4
        while time.monotonic() < deadline:
            body = json.dumps(
                {
                    "run_id": run.run_id,
                    "written_at": datetime.now(timezone.utc).isoformat(),
                }
            ).encode()
            store.put(BUCKET, key, body)
            assert get_run(db_path, run.run_id).status == "running"
            time.sleep(0.15)
        assert get_run(db_path, run.run_id).status == "running"
        api.runs[hermes_id]["status"] = "completed"
        store.put(
            BUCKET,
            f"optimization/runs/{run.run_id}/output/result.json",
            json.dumps(_NULL_RESULT).encode(),
        )
        wait_for_run(run.run_id, timeout=10)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "succeeded"
    assert finished.reason == "No change."


def test_a_refused_connection_then_interrupted_frees_the_next_run(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "running"

        api.on_accept = publish
        api.events_body = b": keepalive\n\n"
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(**{**options.__dict__, "timeout_seconds": 30})
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)

        def polled() -> bool:
            stored = get_run(db_path, run.run_id)
            if not stored.hermes_run_id:
                return False
            return any(
                call.method == "GET" and call.path == f"/v1/runs/{stored.hermes_run_id}"
                for call in api.calls
            )

        _wait_until(polled)
        api.suspend()
        time.sleep(0.3)
        api.resume()
        current = get_run(db_path, run.run_id)
        assert current.hermes_run_id is not None
        api.runs[current.hermes_run_id]["status"] = "interrupted"
        api.runs[current.hermes_run_id].pop("error", None)
        wait_for_run(run.run_id, timeout=10)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == HERMES_INTERRUPTED_REASON
    settings = _hermes_settings(tmp_path, api)
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        started = client.post("/admin/optimization/run", json={})
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "skipped"


def test_timeout_stops_the_hermes_run_and_marks_timed_out(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "running"

        api.on_accept = publish
        api.events_body = b": keepalive\n\n"
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(
            **{
                **options.__dict__,
                "timeout_seconds": 0.4,
                "heartbeat_stall_seconds": 30,
                "hermes_stop_wait_seconds": 0.3,
            }
        )
        started = time.monotonic()
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
        wait_for_run(run.run_id, timeout=10)
        elapsed = time.monotonic() - started
        calls = list(api.calls)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason == "Timed out"
    assert elapsed < 5
    assert any(call.path.endswith("/stop") for call in calls)
    hermes_id = finished.hermes_run_id
    assert hermes_id is not None
    assert api.runs[hermes_id]["status"] == "stopping"


def test_step_preview_is_truncated_and_redacted(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    preview = API_KEY + ("x" * 600)
    with HermesApi() as api:
        api.events_body = (
            _frame(1, "tool.started", tool="terminal", preview=preview)
            + b": keepalive\n\n"
            + _frame(2, "run.completed", output="done")
        )

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "completed"
            _store(tmp_path).put(
                BUCKET,
                f"optimization/runs/{deepfix_id}/output/result.json",
                json.dumps(_NULL_RESULT).encode(),
            )

        api.on_accept = publish
        run = start_optimization_run(
            db_path,
            workspace,
            trigger="manual",
            options=_hermes_options(tmp_path, api),
        )
        wait_for_run(run.run_id)
    logged = (tmp_path / "private" / "optimization-logs" / f"{run.run_id}.log").read_text(
        encoding="utf-8"
    )
    assert API_KEY not in logged
    assert "[redacted]" in logged
    line = next(item for item in logged.splitlines() if "event=tool.started" in item)
    preview_text = line.split("preview=", 1)[1]
    assert len(preview_text) == 500
    assert "event=keepalive" not in logged


def test_startup_asks_hermes_before_failing_a_stored_run(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    previous = _bundle_env(tmp_path)
    try:
        with HermesApi() as api:
            hermes_id = "run_stored"
            api.runs[hermes_id] = {
                "object": "hermes.run",
                "run_id": hermes_id,
                "status": "running",
            }
            api.events_body = b": keepalive\n\n"
            settings = _hermes_settings(tmp_path, api)
            _insert_hermes_row(
                settings.product_db_path,
                "stored",
                hermes_run_id=hermes_id,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
                identity.sign_in(client, "admin@example.com", "admin")
                _wait_until(
                    lambda: any(
                        call.method == "GET" and call.path == f"/v1/runs/{hermes_id}"
                        for call in api.calls
                    )
                )
                assert not any(
                    call.method == "POST" and call.path == "/v1/runs" for call in api.calls
                )
                assert get_run(settings.product_db_path, "stored").status == "running"
                api.runs[hermes_id]["status"] = "completed"
                _store(tmp_path).put(
                    BUCKET,
                    "optimization/runs/stored/output/result.json",
                    json.dumps(_NULL_RESULT).encode(),
                )
                wait_for_run("stored", timeout=20)
            finished = get_run(settings.product_db_path, "stored")
            assert finished.status == "succeeded"
            assert finished.hermes_run_id == hermes_id
            posts = [
                call for call in api.calls if call.method == "POST" and call.path == "/v1/runs"
            ]
            assert posts == []
    finally:
        _restore_env(previous)


def test_startup_resolves_unknown_and_an_unreachable_hermes_stays_running(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    with HermesApi() as api:
        hermes_id = "run_unknown"
        api.runs[hermes_id] = {
            "object": "hermes.run",
            "run_id": hermes_id,
            "status": "unknown",
            "admission_id": "adm_abc123",
            "execution_generation": 4,
            "error": "Gateway shutdown interrupted the run.",
        }
        settings = _hermes_settings(tmp_path, api)
        _insert_hermes_row(
            settings.product_db_path,
            "lost",
            hermes_run_id=hermes_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
            identity.sign_in(client, "admin@example.com", "admin")
            status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        run = status.json()["runs"][0]
        assert run["status"] == "failed"
        assert run["reason"] == "Gateway shutdown interrupted the run."
        assert any(call.path == f"/v1/runs/{hermes_id}/resolve-unknown" for call in api.calls)
        assert not any(call.method == "POST" and call.path == "/v1/runs" for call in api.calls)

    dark = Settings(
        PRODUCT_DATA_DIR=tmp_path / "dark",
        WORKSPACE_DIR=tmp_path / "dark-workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        OPENHANDS_PYTHON=None,
        OPTIMIZATION_AGENT="hermes",
        HERMES_BASE_URL="http://127.0.0.1:1",
        API_SERVER_KEY=API_KEY,
    )
    _insert_hermes_row(
        dark.product_db_path,
        "quiet",
        hermes_run_id="run_quiet",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    with TestClient(create_app(dark, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
    assert status.status_code == 200, status.text
    quiet = status.json()["runs"][0]
    assert quiet["run_id"] == "quiet"
    assert quiet["status"] == "running"
    assert quiet["reason"] is None


def test_a_second_process_reuses_the_idempotency_key(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    previous = _bundle_env(tmp_path)
    try:
        with HermesApi() as api:
            api.events_body = b": keepalive\n\n"
            settings = _hermes_settings(tmp_path, api)
            _insert_hermes_row(
                settings.product_db_path,
                "again",
                hermes_run_id=None,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            with TestClient(create_app(settings, token_verifier=identity.verifier())):
                _wait_until(
                    lambda: get_run(settings.product_db_path, "again").hermes_run_id is not None
                )
            posts = [
                call for call in api.calls if call.method == "POST" and call.path == "/v1/runs"
            ]
            assert len(posts) == 1
            assert posts[0].headers.get("Idempotency-Key") == "again"
            assert get_run(settings.product_db_path, "again").status == "running"
            with TestClient(create_app(settings, token_verifier=identity.verifier())):
                time.sleep(0.3)
            posts = [
                call for call in api.calls if call.method == "POST" and call.path == "/v1/runs"
            ]
            assert len(posts) == 1
            with connect(settings.product_db_path) as conn:
                count = conn.execute("SELECT count(*) AS n FROM optimization_runs").fetchone()["n"]
            assert count == 1
            hermes_id = get_run(settings.product_db_path, "again").hermes_run_id
            assert hermes_id is not None
            api.runs[hermes_id]["status"] = "interrupted"
            # Resume polling uses the 15s status cadence, so the next read can take that long.
            wait_for_run("again", timeout=20)
            assert get_run(settings.product_db_path, "again").status == "failed"
    finally:
        _restore_env(previous)


def test_admin_cancel_stops_hermes_and_members_cannot(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    previous = _bundle_env(tmp_path)
    try:
        workspace, db_path = _workspace(tmp_path, "public statute\n")
        with HermesApi() as api:

            def publish(hermes_id: str, deepfix_id: str) -> None:
                api.runs[hermes_id]["status"] = "running"

            api.on_accept = publish
            api.events_body = b": keepalive\n\n"
            options = _hermes_options(tmp_path, api)
            options = OptimizationSettings(
                **{**options.__dict__, "timeout_seconds": 30, "heartbeat_stall_seconds": 30}
            )
            run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
            _wait_until(lambda: get_run(db_path, run.run_id).hermes_run_id is not None)
            settings = _hermes_settings(tmp_path, api)
            with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
                missing = client.post(f"/admin/optimization/runs/{run.run_id}/cancel")
                assert missing.status_code == 401
                identity.sign_in(client, "ada@example.com")
                forbidden = client.post(f"/admin/optimization/runs/{run.run_id}/cancel")
                assert forbidden.status_code == 403
                detail = "Only admins can cancel an optimization run."
                assert forbidden.json()["detail"] == detail
                identity.sign_in(client, "admin@example.com", "admin")
                cancelled = client.post(f"/admin/optimization/runs/{run.run_id}/cancel")
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["status"] == "failed"
                assert cancelled.json()["reason"] == CANCELLED_REASON
                again = client.post(f"/admin/optimization/runs/{run.run_id}/cancel")
                assert again.status_code == 409
                unknown = client.post("/admin/optimization/runs/missing/cancel")
                assert unknown.status_code == 404
            wait_for_run(run.run_id, timeout=10)
            assert any(call.path.endswith("/stop") for call in api.calls)
        assert get_run(db_path, run.run_id).reason == CANCELLED_REASON
    finally:
        _restore_env(previous)


def test_status_stops_a_stale_hermes_run(tmp_path: Path, identity: IdentityIssuer) -> None:
    _tracking(tmp_path)
    with HermesApi() as api:
        hermes_id = "run_stale"
        api.runs[hermes_id] = {"run_id": hermes_id, "status": "running"}
        settings = _hermes_settings(tmp_path, api)
        with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
            identity.sign_in(client, "admin@example.com", "admin")
            started = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
            _insert_hermes_row(
                settings.product_db_path,
                "old",
                hermes_run_id=hermes_id,
                started_at=started,
            )
            status = client.get("/admin/optimization")
            assert status.status_code == 200, status.text
            run = status.json()["runs"][0]
            assert run["run_id"] == "old"
            assert run["status"] == "failed"
            assert run["reason"] == STALE_REASON
        assert any(call.path == f"/v1/runs/{hermes_id}/stop" for call in api.calls)


def test_a_fresh_heartbeat_is_a_stderr_line_and_the_log_stays_private(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, identity: IdentityIssuer
) -> None:
    _tracking(tmp_path)
    caplog.set_level(logging.INFO, logger="DeepFix RAG Eval Runner")
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    begin = "BEGIN-OUTPUT-" + ("a" * 100)
    end = "END-OF-OUTPUT-MARKER"
    output = begin + ("b" * (70 * 1024)) + end
    secret = "scoped-secret-value"
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "running"

        api.on_accept = publish
        api.events_body = b": keepalive\n\n"
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(
            **{
                **options.__dict__,
                "heartbeat_stall_seconds": 30,
                "timeout_seconds": 30,
                "source_env": {
                    **options.source_env,
                    "S3_SECRET_ACCESS_KEY": secret,
                },
            }
        )
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)

        def stored() -> bool:
            return bool(get_run(db_path, run.run_id).hermes_run_id)

        _wait_until(stored)
        store = _store(tmp_path)
        store.put(
            BUCKET,
            f"optimization/runs/{run.run_id}/output/heartbeat.json",
            json.dumps(
                {
                    "run_id": run.run_id,
                    "pid": 42,
                    "elapsed_s": 1,
                    "argv0": "deepfix",
                    "written_at": datetime.now(timezone.utc).isoformat(),
                }
            ).encode(),
        )
        log_path = tmp_path / "private" / "optimization-logs" / f"{run.run_id}.log"

        def logged_heartbeat() -> bool:
            if not log_path.is_file():
                return False
            return "event=heartbeat" in log_path.read_text(encoding="utf-8")

        _wait_until(logged_heartbeat)
        hermes_id = get_run(db_path, run.run_id).hermes_run_id
        assert hermes_id is not None
        api.runs[hermes_id]["status"] = "completed"
        api.runs[hermes_id]["output"] = output + secret
        api.runs[hermes_id]["error"] = None
        store.put(
            BUCKET,
            f"optimization/runs/{run.run_id}/output/result.json",
            json.dumps({**_NULL_RESULT, "mlflow_run_id": "mlflow-eval-run-1"}).encode(),
        )
        wait_for_run(run.run_id, timeout=10)
    logged = log_path.read_text(encoding="utf-8")
    assert "event=heartbeat" in logged
    assert "event=heartbeat" in caplog.text
    assert any(
        isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr
        for handler in logging.getLogger("DeepFix RAG Eval Runner").handlers
    )
    assert begin in logged
    assert end not in logged
    assert secret not in logged
    assert "----- output -----" in logged
    assert not log_path.is_relative_to(workspace)
    bodies = b"\n".join(_bodies(_store(tmp_path), run.run_id))
    assert begin.encode() not in bodies
    assert b"optimization-logs" not in bodies
    finished = get_run(db_path, run.run_id)
    assert finished.mlflow_run_id == "mlflow-eval-run-1"
    settings = _hermes_settings(tmp_path, api)
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        identity.sign_in(client, "ada@example.com")
        forbidden = client.get("/admin/optimization")
        assert forbidden.status_code == 403
        assert begin not in forbidden.text
        identity.sign_in(client, "admin@example.com", "admin")
        status = client.get("/admin/optimization")
        assert status.status_code == 200, status.text
        row = next(item for item in status.json()["runs"] if item["run_id"] == run.run_id)
        assert row["mlflow_run_id"] == "mlflow-eval-run-1"
        assert begin not in status.text


def test_a_hermes_error_keeps_the_text_and_drops_secrets(tmp_path: Path) -> None:
    _tracking(tmp_path)
    workspace, db_path = _workspace(tmp_path, "public statute\n")
    secret = "scoped-secret-value"
    openrouter = "sk-or-" + ("k" * 16)
    private = str(tmp_path / "private")
    with HermesApi() as api:

        def publish(hermes_id: str, deepfix_id: str) -> None:
            api.runs[hermes_id]["status"] = "failed"
            api.runs[hermes_id]["error"] = f"provider failed {secret} {openrouter} at {private}"

        api.on_accept = publish
        api.events_body = _frame(1, "run.failed", error=f"provider failed {secret} {openrouter}")
        options = _hermes_options(tmp_path, api)
        options = OptimizationSettings(
            **{
                **options.__dict__,
                "source_env": {
                    **options.source_env,
                    "S3_SECRET_ACCESS_KEY": secret,
                    "OPENROUTER_API_KEY": openrouter,
                },
                "private_paths": (private,),
            }
        )
        run = start_optimization_run(db_path, workspace, trigger="manual", options=options)
        wait_for_run(run.run_id)
    finished = get_run(db_path, run.run_id)
    assert finished.status == "failed"
    assert finished.reason is not None
    assert "provider failed" in finished.reason
    assert "[redacted]" in finished.reason
    assert secret not in finished.reason
    assert private not in finished.reason
    assert openrouter not in finished.reason

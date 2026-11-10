"""MLflow traces for a Hermes turn. The tracking store is a local sqlite file."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import mlflow
import pytest

from deepfix_rag.hermes_traces import HERMES_EXPERIMENT, RUN_ID_TAG, export_turn

pytestmark = [pytest.mark.unit]

SECRET = "scoped-secret-value"
PLUGIN = (
    Path(__file__).resolve().parents[1] / "docker" / "hermes" / "deepfix_traces" / "__init__.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("deepfix_traces_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trace_blob(trace_id: str) -> str:
    trace = mlflow.get_trace(trace_id)
    if hasattr(trace, "to_dict"):
        return json.dumps(trace.to_dict(), default=str)
    return json.dumps(trace.info.tags, default=str) + "".join(
        f"{span.name} {span.span_type} {span.attributes} {span.inputs} {span.outputs}"
        for span in trace.data.spans
    )


def test_export_turn_writes_an_agent_span_and_an_llm_span(tmp_path: Path) -> None:
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("keep-me")
    from mlflow.tracking.fluent import _get_experiment_id

    keep = _get_experiment_id()
    try:
        trace_id = export_turn(
            run_id="run-77",
            session_id="sess-1",
            llm_calls=[
                {"model": f"anthropic/claude-haiku-5.5 {SECRET}", "ok": True},
                {"model": "anthropic/claude-haiku-5.5", "ok": False},
            ],
            secrets=(SECRET,),
        )
        mlflow.flush_trace_async_logging()
        trace = mlflow.get_trace(trace_id)
        assert trace.info.tags[RUN_ID_TAG] == "run-77"
        spans = {span.name: span for span in trace.data.spans}
        assert spans["hermes.agent"].span_type == "AGENT"
        assert spans["hermes.llm"].span_type == "LLM"
        assert spans["hermes.llm"].parent_id == spans["hermes.agent"].span_id
        blob = _trace_blob(trace_id)
        assert SECRET not in blob
        assert "[redacted]" in blob
        assert mlflow.get_experiment_by_name(HERMES_EXPERIMENT) is not None
        assert _get_experiment_id() == keep
    finally:
        mlflow.set_tracking_uri(previous_uri)


def test_the_cli_reads_one_turn_from_stdin(tmp_path: Path) -> None:
    previous_uri = mlflow.get_tracking_uri()
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "MLFLOW_TRACKING_URI": uri,
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "S3_SECRET_ACCESS_KEY": SECRET,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "deepfix_rag.hermes_traces"],
            input=json.dumps(
                {
                    "run_id": "run-cli",
                    "session_id": "sess",
                    "llm_calls": [{"model": "anthropic/claude-haiku-5.5", "ok": True}],
                    "request": SECRET,
                }
            ).encode(),
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        mlflow.flush_trace_async_logging()
        experiment = mlflow.get_experiment_by_name(HERMES_EXPERIMENT)
        assert experiment is not None
        traces = mlflow.search_traces(locations=[experiment.experiment_id])
        assert len(traces) == 1
        trace_id = traces.iloc[0]["trace_id"]
        trace = mlflow.get_trace(trace_id)
        assert trace.info.tags[RUN_ID_TAG] == "run-cli"
        names = {span.name for span in trace.data.spans}
        assert names == {"hermes.agent", "hermes.llm"}
        assert SECRET not in _trace_blob(trace_id)
    finally:
        mlflow.set_tracking_uri(previous_uri)


def test_plugin_hooks_export_a_turn_and_drop_request_bodies(tmp_path: Path) -> None:
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    plugin = _load_plugin()
    plugin.reset_state()
    message = (
        "The DeepFix run id is run-77. "
        f"Read /opt/data/workspace/runs/run-77/input/champion.yaml. key {SECRET}"
    )
    try:
        plugin._on_pre_api_request(
            session_id="sess",
            model="anthropic/claude-haiku-5.5",
            user_message=message,
            request={"model": "anthropic/claude-haiku-5.5", "messages": [{"content": SECRET}]},
        )
        plugin._on_post_api_request(session_id="sess", model="anthropic/claude-haiku-5.5")
        plugin._on_post_llm_call(
            session_id="sess",
            user_message=message,
            assistant_response=SECRET,
            model="anthropic/claude-haiku-5.5",
        )
        mlflow.flush_trace_async_logging()
        experiment = mlflow.get_experiment_by_name(HERMES_EXPERIMENT)
        assert experiment is not None
        traces = mlflow.search_traces(locations=[experiment.experiment_id])
        assert len(traces) == 1
        trace = mlflow.get_trace(traces.iloc[0]["trace_id"])
        assert trace.info.tags[RUN_ID_TAG] == "run-77"
        kinds = {span.span_type for span in trace.data.spans}
        assert {"AGENT", "LLM"} <= kinds
        assert SECRET not in _trace_blob(
            trace.trace_id if hasattr(trace, "trace_id") else traces.iloc[0]["trace_id"]
        )
    finally:
        plugin.reset_state()
        mlflow.set_tracking_uri(previous_uri)


def test_plugin_falls_back_to_the_deepfix_interpreter_outside_that_venv(tmp_path: Path) -> None:
    system = Path("/usr/bin/python3")
    if not system.is_file():
        pytest.skip("system python is not installed")
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    driver = tmp_path / "drive.py"
    driver.write_text(
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('deepfix_traces', {str(PLUGIN)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "module._on_pre_api_request(\n"
        "    session_id='sess',\n"
        "    model='anthropic/claude-haiku-5.5',\n"
        "    user_message='The DeepFix run id is run-outside.',\n"
        ")\n"
        "module._on_post_api_request(session_id='sess')\n"
        "module._on_post_llm_call(\n"
        "    session_id='sess',\n"
        "    user_message='The DeepFix run id is run-outside.',\n"
        "    assistant_response='',\n"
        ")\n",
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DEEPFIX_PYTHON": sys.executable,
        "MLFLOW_TRACKING_URI": uri,
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "S3_SECRET_ACCESS_KEY": SECRET,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [str(system), str(driver)],
        env=env,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        experiment = mlflow.get_experiment_by_name(HERMES_EXPERIMENT)
        assert experiment is not None
        traces = mlflow.search_traces(locations=[experiment.experiment_id])
        assert len(traces) == 1
        trace = mlflow.get_trace(traces.iloc[0]["trace_id"])
        assert trace.info.tags[RUN_ID_TAG] == "run-outside"
        assert {span.span_type for span in trace.data.spans} >= {"AGENT", "LLM"}
        assert SECRET not in _trace_blob(traces.iloc[0]["trace_id"])
    finally:
        mlflow.set_tracking_uri(previous_uri)


def test_cli_rejects_a_payload_that_is_not_a_turn() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "deepfix_rag.hermes_traces"],
        input=b"[]",
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1

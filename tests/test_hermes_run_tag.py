"""The run-id stamp and the deepfix_hermes experiment. No Hermes process."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import mlflow
import pytest

from deepfix_rag.hermes_trace_experiment import (
    HERMES_TRACE_EXPERIMENT,
    ensure_hermes_trace_experiment,
)

pytestmark = [pytest.mark.unit]

PLUGIN = (
    Path(__file__).resolve().parents[1] / "docker" / "hermes" / "deepfix_run_tag" / "__init__.py"
)
SECRET = "scoped-secret-value"


def _load():
    spec = importlib.util.spec_from_file_location("deepfix_run_tag_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.reset_state()
    return module


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


class _Spans:
    def __init__(self, root: _Span, parent: _Span) -> None:
        self.root = root
        self.parent = parent

    def get_session_root(self, session_id: str) -> _Span:
        return self.root

    def get_current_parent(self, session_id: str) -> _Span:
        return self.parent


def test_the_turn_message_sets_only_the_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load()
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", SECRET)
    message = (
        "The DeepFix run id is run-77. "
        f"Read /opt/data/workspace/runs/run-77/input/champion.yaml. key {SECRET}"
    )
    plugin._on_turn(
        session_id="sess", user_message=message, request={"messages": [{"content": SECRET}]}
    )
    assert plugin._run_id == "run-77"
    assert SECRET not in plugin._run_id


def test_open_spans_receive_the_run_id_and_not_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-" + ("k" * 16))
    root = _Span()
    parent = _Span()

    class _Tracer:
        spans = _Spans(root, parent)

    plugin._stamp_open_spans("sess", "run-77", _Tracer())
    assert root.attributes == {"deepfix.run_id": "run-77"}
    assert parent.attributes == {"deepfix.run_id": "run-77"}
    assert "sk-or-" not in str(root.attributes)

    fresh = _Span()
    fresh.attributes["hermes.tool.command"] = "deepfix eval " + os.environ["OPENROUTER_API_KEY"]
    plugin._run_id = "run-77"
    plugin._RunIdProcessor().on_start(fresh)
    assert fresh.attributes["deepfix.run_id"] == "run-77"
    assert os.environ["OPENROUTER_API_KEY"] not in fresh.attributes["hermes.tool.command"]
    assert "[redacted]" in fresh.attributes["hermes.tool.command"]
    fresh.attributes["deepfix.run_id"] = "keep"
    plugin._RunIdProcessor().on_start(fresh)
    assert fresh.attributes["deepfix.run_id"] == "keep"


def test_a_secret_is_not_accepted_as_a_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load()
    monkeypatch.setenv("API_SERVER_KEY", "a" * 32)
    assert plugin._acceptable("a" * 32) is False
    assert (
        plugin._find_run_id({"user_message": "The DeepFix run id is " + ("a" * 32) + "."}) is None
    )
    assert plugin._find_run_id({"user_message": "The DeepFix run id is run-ok."}) == "run-ok"


def test_deepfix_hermes_experiment_is_created_once(tmp_path: Path) -> None:
    previous = mlflow.get_tracking_uri()
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    try:
        first = ensure_hermes_trace_experiment(uri)
        second = ensure_hermes_trace_experiment(uri)
        assert first and first == second
        mlflow.set_tracking_uri(uri)
        experiment = mlflow.get_experiment(first)
        assert experiment.name == HERMES_TRACE_EXPERIMENT
        assert mlflow.get_tracking_uri() == uri
    finally:
        mlflow.set_tracking_uri(previous)
        os.environ.pop("MLFLOW_HTTP_REQUEST_TIMEOUT", None)

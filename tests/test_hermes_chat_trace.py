"""Chat tracing: the chat profile's own experiment and the two span labels.

The plugin hooks and the boot script are exercised directly; the exporter
itself is hermes-otel's, so a real span object is a small recording double, which
is what the existing ``tests/test_hermes_run_tag.py`` does too.
"""

import importlib.util
from pathlib import Path
import tempfile

import mlflow
from mlflow.tracking import MlflowClient
import pytest
import yaml

from deepfix_rag.hermes_trace_experiment import (
    HERMES_CHAT_TRACE_EXPERIMENT,
    HERMES_TRACE_EXPERIMENT,
    ensure_hermes_trace_experiment,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "docker" / "hermes" / "deepfix_run_tag" / "__init__.py"
_SCRATCH = Path(tempfile.mkdtemp(prefix="deepfix-chat-trace-test-"))
CHAT_KEY = "b" * 32


def _plugin():
    spec = importlib.util.spec_from_file_location("deepfix_run_tag_chat", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.reset_state()
    return module


def _boot():
    spec = importlib.util.spec_from_file_location(
        "hermes_sandbox_boot_trace", REPO_ROOT / "docker" / "hermes" / "sandbox_boot.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Span:
    def __init__(self, **attributes: str) -> None:
        self.attributes: dict[str, str] = dict(attributes)

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


def _prompt(thread_id: str, turn_id: str) -> str:
    return (
        "You are the DeepFix admin chat.\n\n"
        f"The DeepFix chat thread id is {thread_id} and the turn id is {turn_id}.\n"
    )


def test_the_two_experiment_names_differ() -> None:
    assert HERMES_TRACE_EXPERIMENT == "deepfix_hermes"
    assert HERMES_CHAT_TRACE_EXPERIMENT == "deepfix_hermes_chat"
    assert HERMES_TRACE_EXPERIMENT != HERMES_CHAT_TRACE_EXPERIMENT


def test_the_helper_creates_the_named_experiment(tmp_path: Path) -> None:
    tracking = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(tracking)
    chat_id = ensure_hermes_trace_experiment(tracking, name=HERMES_CHAT_TRACE_EXPERIMENT)
    assert chat_id
    # The optimizer's call keeps its default and its own experiment.
    optimizer_id = ensure_hermes_trace_experiment(tracking)
    assert optimizer_id and optimizer_id != chat_id
    names = {
        experiment.name for experiment in MlflowClient(tracking_uri=tracking).search_experiments()
    }
    assert {HERMES_CHAT_TRACE_EXPERIMENT, HERMES_TRACE_EXPERIMENT} <= names


def test_the_plugin_stamps_the_chat_labels_on_a_turn() -> None:
    plugin = _plugin()
    span = _Span()
    plugin._on_turn(messages=[{"role": "user", "content": _prompt("thread-1", "turn-1")}])
    plugin._scrub_span(span)
    assert span.attributes["deepfix.chat_thread_id"] == "thread-1"
    assert span.attributes["deepfix.chat_turn_id"] == "turn-1"
    assert "deepfix.run_id" not in span.attributes


def test_the_plugin_reads_the_labels_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    monkeypatch.setenv("DEEPFIX_CHAT_THREAD_ID", "thread-2")
    monkeypatch.setenv("DEEPFIX_CHAT_TURN_ID", "turn-2")
    span = _Span()
    plugin._on_turn(messages=[])
    plugin._scrub_span(span)
    assert span.attributes["deepfix.chat_thread_id"] == "thread-2"
    assert span.attributes["deepfix.chat_turn_id"] == "turn-2"


def test_the_optimization_run_id_still_works_and_is_not_a_chat_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    # A chat key from another case must not make this run id look like a secret.
    monkeypatch.delenv("HERMES_CHAT_API_SERVER_KEY", raising=False)
    span = _Span()
    plugin._on_turn(user_message="The DeepFix run id is run-77.")
    plugin._scrub_span(span)
    assert span.attributes["deepfix.run_id"] == "run-77"
    assert "deepfix.chat_thread_id" not in span.attributes


def test_a_chat_span_carries_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin()
    monkeypatch.setenv("HERMES_CHAT_API_SERVER_KEY", CHAT_KEY)
    span = _Span(**{"hermes.tool.command": f"curl -H 'Authorization: {CHAT_KEY}' http://x"})
    plugin._on_turn(messages=[{"role": "user", "content": _prompt("thread-3", "turn-3")}])
    plugin._scrub_span(span)
    values = " ".join(span.attributes.values())
    assert CHAT_KEY not in values
    assert "[redacted]" in values
    assert span.attributes["deepfix.chat_thread_id"] == "thread-3"


def test_the_chat_profile_gets_its_own_document_and_plugins(tmp_path: Path) -> None:
    boot = _boot()
    home = tmp_path / "data"
    otel = tmp_path / "hermes_otel"
    tag = tmp_path / "deepfix-run-tag"
    for directory in (otel, tag):
        directory.mkdir()
        (directory / "plugin.yaml").write_text(f"name: {directory.name}\n", encoding="utf-8")
        (directory / "__init__.py").write_text(
            "def register(ctx):\n    return None\n", encoding="utf-8"
        )
    skills = tmp_path / "skills"
    (skills / "optimization-loop").mkdir(parents=True)
    (skills / "optimization-loop" / "SKILL.md").write_text(
        "---\nname: optimization-loop\n---\n", encoding="utf-8"
    )
    boot.apply_sandbox(
        home,
        {
            "API_SERVER_KEY": "a" * 32,
            "HERMES_CHAT_API_SERVER_KEY": CHAT_KEY,
            "HERMES_MODEL": "fixture/model",
            "HERMES_CHAT_MODEL": "fixture/chat-model",
            "S3_ACCESS_KEY_ID": "hermes-optimizer",
            "S3_SECRET_ACCESS_KEY": "scoped-secret-value",
            "DEEPFIX_SKIP_TRACE_EXPERIMENT": "1",
            "DEEPFIX_HERMES_EXPERIMENT_ID": "11",
            "DEEPFIX_HERMES_CHAT_EXPERIMENT_ID": "22",
            "HERMES_OTEL_PACKAGE": str(otel),
            "DEEPFIX_RUN_TAG_PLUGIN": str(tag),
            "DEEPFIX_EVAL_ENV": str(_SCRATCH / "eval-env"),
        },
        skills,
    )
    optimizer_document = yaml.safe_load((home / "hermes_otel.yaml").read_text(encoding="utf-8"))
    chat_document = yaml.safe_load(
        (home / "profiles" / "chat" / "hermes_otel.yaml").read_text(encoding="utf-8")
    )
    assert optimizer_document["backends"][0]["experiment_id"] == "11"
    assert chat_document["backends"][0]["experiment_id"] == "22"
    for document in (optimizer_document, chat_document):
        assert document["content_capture"] == "off"
        assert document["capture_conversation_history"] is False
        assert document["capture_sender_id"] is False
        assert document["backends"][0]["type"] == "mlflow"
        assert document["backends"][0]["metrics"] is False
        assert document["backends"][0]["logs"] is False
    chat_config = yaml.safe_load(
        (home / "profiles" / "chat" / "config.yaml").read_text(encoding="utf-8")
    )
    assert chat_config["plugins"]["enabled"] == ["hermes_otel", "deepfix-run-tag"]
    # The chat profile's plugin links live inside its own profile directory.
    assert (home / "profiles" / "chat" / "plugins" / "hermes_otel").is_symlink()


def test_the_chat_experiment_name_is_a_setting_with_a_safe_default() -> None:
    boot = _boot()
    assert boot.chat_experiment_name({}) == "deepfix_hermes_chat"
    assert boot.chat_experiment_name({"MLFLOW_HERMES_CHAT_EXPERIMENT": ""}) == (
        "deepfix_hermes_chat"
    )
    assert boot.chat_experiment_name({"MLFLOW_HERMES_CHAT_EXPERIMENT": "custom"}) == "custom"
    with pytest.raises(boot.SandboxBootError):
        boot.chat_experiment_name({"MLFLOW_HERMES_CHAT_EXPERIMENT": "bad\nname"})


def test_the_chat_document_never_carries_the_chat_key(tmp_path: Path) -> None:
    boot = _boot()
    document = boot.hermes_otel_document(
        {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}"}, "22"
    )
    text = yaml.safe_dump(document)
    assert CHAT_KEY not in text
    assert "content_capture: 'off'" in text or 'content_capture: "off"' in text

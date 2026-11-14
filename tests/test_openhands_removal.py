"""US-010: OpenHands is gone and Hermes is the only optimization agent.

These assert the removal, not the behaviour. They are the guard that stops the
OpenHands path, its service, or its variables from creeping back in.
"""

from pathlib import Path
import re

from pydantic import ValidationError
import pytest
import yaml

from deepfix_rag.config.settings import Settings
from deepfix_rag.optimization import OptimizationAgent

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "deepfix_rag"

# Every name the OpenHands path used. None may be a setting after US-010.
RETIRED_SETTINGS = (
    "OPENHANDS_PYTHON",
    "OH_SESSION_HOST",
    "DEEPFIX_LLM_API_KEY",
    "DEEPFIX_LLM_BASE_URL",
    "DEEPFIX_LLM_MODEL_NAME",
    "OPTIMIZATION_GOAL_ROUNDS",
)


def test_the_openhands_module_and_runner_are_deleted() -> None:
    assert not (SRC / "agent_process.py").exists()
    assert not (REPO_ROOT / "scripts" / "openhands_runner.py").exists()
    assert not (REPO_ROOT / "docker" / "openhands-agent-requirements.txt").exists()
    assert not (REPO_ROOT / "compose.openhands.yml").exists()
    assert not (REPO_ROOT / "tasks" / "notes-openhands-integration.md").exists()


@pytest.mark.parametrize("name", RETIRED_SETTINGS)
def test_the_retired_settings_are_gone(name: str) -> None:
    assert Settings.model_fields.get(name) is None, f"{name} is still a setting"


def test_hermes_is_the_only_agent_value() -> None:
    assert [agent.value for agent in OptimizationAgent] == ["hermes"]
    assert Settings.model_fields["OPTIMIZATION_AGENT"].default == "hermes"
    for stale in ("openhands", "both"):
        with pytest.raises(ValidationError):
            Settings(OPTIMIZATION_AGENT=stale)


def test_the_package_does_not_import_the_deleted_module() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if "agent_process" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_compose_has_no_openhands_service_or_volume() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "openhands" not in compose["services"]
    assert "openhands_data" not in compose["volumes"]
    included = compose.get("include", [])
    assert "compose.openhands.yml" not in included
    assert "compose.hermes.yml" in included
    env = compose["services"]["backend-api"]["environment"]
    assert env["OPTIMIZATION_AGENT"] == "${OPTIMIZATION_AGENT:-hermes}"
    for retired in ("OPENHANDS_PYTHON", "OH_SESSION_HOST", "OPTIMIZATION_GOAL_ROUNDS"):
        assert retired not in env, retired


def test_the_api_image_no_longer_builds_an_openhands_venv() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "openhands" not in dockerfile.lower()
    assert "/opt/openhands-agent" not in dockerfile


def test_env_docs_do_not_offer_the_openhands_variables() -> None:
    """The variables the removed OpenHands child used must not be offered anywhere."""
    text = (REPO_ROOT / "example.env").read_text(encoding="utf-8")
    for retired in ("OPENHANDS_PYTHON", "OH_SESSION_HOST", "OPTIMIZATION_GOAL_ROUNDS"):
        assert not re.search(rf"(?m)^{retired}=", text), retired
    # The diagnostic-agent keeps its own LiteLLM settings and does not import
    # this package, so DEEPFIX_LLM_* survives for it only. It must not be
    # presented as the API's agent model.
    assert "DEEPFIX_SEARCH_PROVIDER" in text
    assert "OpenHands" not in text


def test_the_hermes_note_replaces_the_openhands_one() -> None:
    note = (REPO_ROOT / "tasks" / "notes-hermes-agent.md").read_text(encoding="utf-8")
    assert "e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c" in note
    assert "http://hermes:8642" in note
    assert "OPTIMIZATION_AGENT" in note
    for name in ("API_SERVER_KEY", "HERMES_MODEL", "OPENROUTER_API_KEY"):
        assert name in note

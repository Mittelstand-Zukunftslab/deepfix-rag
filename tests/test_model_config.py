"""Model ids are deployment configuration: env template, Compose, and code defaults.

The package has no model default. The values live in ``example.env`` and the
Compose files, which is what this module pins.
"""

from pathlib import Path
import re

import pytest

from deepfix_rag.config.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV_NAMES = (
    "LLM_MODEL",
    "LLM_JUDGE_MODEL",
    "HERMES_MODEL",
    "EMBEDDING_MODEL",
    "COHERE_MODEL",
    "DEEPFIX_LLM_MODEL_NAME",
)
# Model ids that used to be compiled into the package. None may reappear.
RETIRED_LITERALS = (
    "anthropic/claude-",
    "openai/gpt-",
    "perplexity/pplx-",
    "cohere/rerank-",
)


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / "example.env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value.strip()
    return values


@pytest.mark.unit
def test_example_env_sets_every_model_the_code_reads() -> None:
    """A fresh `cp example.env .env` must satisfy every model lookup."""
    values = _env_example()
    for name in MODEL_ENV_NAMES:
        assert values.get(name), f"example.env does not set {name}"


@pytest.mark.unit
def test_example_env_carries_the_deployment_model_ids() -> None:
    values = _env_example()
    assert values["HERMES_MODEL"] == "deepseek/deepseek-v4.1-flash-1017"
    assert values["LLM_MODEL"] == "qwen/qwen3.8-flash"
    # The judge may differ from answer generation; this deployment uses an OpenRouter id.
    assert values["LLM_JUDGE_MODEL"] == "z-ai/glm-5.3-flash"


@pytest.mark.unit
def test_the_example_env_judge_is_not_the_answer_model() -> None:
    """The judge is a separate env var, so the template must not collapse the two."""
    text = (REPO_ROOT / "example.env").read_text(encoding="utf-8")
    assert re.search(r"(?im)^#.*judge.*may differ", text)


@pytest.mark.unit
def test_no_model_id_is_a_code_default() -> None:
    for name in MODEL_ENV_NAMES:
        assert Settings.model_fields.get(name) is not None, f"{name} is not a setting"
        assert Settings.model_fields[name].default == "", f"{name} has a code default"


@pytest.mark.unit
def test_retired_model_literals_are_absent_from_the_package() -> None:
    """No model id literal is left in Python source outside tests and fixtures."""
    offenders: list[str] = []
    roots = (
        REPO_ROOT / "src",
        REPO_ROOT / "docker" / "hermes",
        REPO_ROOT / "scripts",
    )
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for literal in RETIRED_LITERALS:
                if literal in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {literal}")
    assert not offenders, offenders

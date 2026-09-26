"""Validate agent skill frontmatter and CLI examples against RAGConfig."""

from pathlib import Path
import re
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel
import pytest
import yaml

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.evaluation.runner import RAGFailureMode

pytestmark = [pytest.mark.unit]

SKILLS_ROOT = Path(__file__).resolve().parents[1] / ".agents" / "skills"
REQUIRED_KEYS = ("name", "description", "failure_mode", "trigger_conditions", "cli_actions")
FAILURE_MODES = {mode.value for mode in RAGFailureMode if mode != RAGFailureMode.SUCCESS}
SET_KEY_RE = re.compile(r"(?:--set|-s)\s+([A-Za-z0-9_.]+)=")


def _skill_paths() -> list[Path]:
    paths = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert paths, f"No SKILL.md files under {SKILLS_ROOT}"
    return paths


def _load_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path} is missing YAML frontmatter")
    _, raw, _body = text.split("---", 2)
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict), f"{path} frontmatter is not a mapping"
    return loaded


def _nested_model(annotation: object) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            found = _nested_model(arg)
            if found is not None:
                return found
    return None


def _config_key_exists(dotted: str) -> bool:
    model: type[BaseModel] = RAGConfig
    parts = dotted.split(".")
    for index, part in enumerate(parts):
        field = model.model_fields.get(part)
        if field is None:
            return False
        if index == len(parts) - 1:
            return True
        nested = _nested_model(field.annotation)
        if nested is None:
            return False
        model = nested
    return False


def test_skill_frontmatter() -> None:
    """Each skill exposes the PRD frontmatter keys and a directory-matching name."""
    for path in _skill_paths():
        meta = _load_frontmatter(path)
        missing = [key for key in REQUIRED_KEYS if key not in meta]
        assert not missing, f"{path.name} missing {missing}"
        assert meta["name"] == path.parent.name
        assert isinstance(meta["description"], str) and meta["description"].strip()
        assert isinstance(meta["failure_mode"], list) and meta["failure_mode"]
        assert isinstance(meta["trigger_conditions"], list) and meta["trigger_conditions"]
        assert isinstance(meta["cli_actions"], list) and meta["cli_actions"]
        unknown = set(meta["failure_mode"]) - FAILURE_MODES
        assert not unknown, f"{path.parent.name} has unknown failure_mode values: {unknown}"


def test_mutate_config_examples_include_base_and_output() -> None:
    """Every mutate-config invocation passes the required --base and --output flags."""
    for path in _skill_paths():
        text = path.read_text(encoding="utf-8")
        invocations = [
            (line_number, line)
            for line_number, line in enumerate(text.splitlines(), start=1)
            if "deepfix mutate-config" in line
        ]
        for line_number, line in invocations:
            assert "--base" in line or "-b " in line, (
                f"{path.parent.name}:{line_number} mutate-config is missing --base"
            )
            assert "--output" in line or "-o " in line, (
                f"{path.parent.name}:{line_number} mutate-config is missing --output"
            )


def test_set_keys_exist_on_rag_config() -> None:
    """Every --set key in a skill resolves to a RAGConfig field."""
    for path in _skill_paths():
        text = path.read_text(encoding="utf-8")
        keys = SET_KEY_RE.findall(text)
        unknown = [key for key in keys if not _config_key_exists(key)]
        assert not unknown, f"{path.parent.name} references unknown config keys: {unknown}"

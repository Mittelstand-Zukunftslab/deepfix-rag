"""Safe YAML mutation logic and configuration diff generation."""

from difflib import get_close_matches
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from deepfix_rag.config.schema import RAGConfig


class UnknownKeyError(ValueError):
    """A dot-notation override does not name a RAGConfig field."""

    def __init__(self, path: str, suggestions: list[str]) -> None:
        self.path = path
        self.suggestions = suggestions
        hint = ""
        if suggestions:
            hint = " Did you mean " + ", ".join(suggestions) + "?"
        super().__init__(f"Unknown config key '{path}'.{hint}")


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


def known_config_paths(model: type[BaseModel] = RAGConfig, prefix: str = "") -> list[str]:
    """Every dotted field path on the config schema."""
    paths: list[str] = []
    for name, field in model.model_fields.items():
        dotted = f"{prefix}.{name}" if prefix else name
        paths.append(dotted)
        nested = _nested_model(field.annotation)
        if nested is not None:
            paths.extend(known_config_paths(nested, dotted))
    return paths


def _require_known_key(dotted: str) -> None:
    model: type[BaseModel] = RAGConfig
    parts = dotted.split(".")
    walked: list[str] = []
    for index, part in enumerate(parts):
        walked.append(part)
        path = ".".join(walked)
        if part not in model.model_fields:
            suggestions = get_close_matches(dotted, known_config_paths(), n=3, cutoff=0.4)
            raise UnknownKeyError(path, suggestions)
        if index == len(parts) - 1:
            return
        nested = _nested_model(model.model_fields[part].annotation)
        if nested is None:
            suggestions = get_close_matches(dotted, known_config_paths(), n=3, cutoff=0.4)
            raise UnknownKeyError(path, suggestions)
        model = nested


def apply_mutation(base_config: RAGConfig, overrides: dict[str, Any]) -> RAGConfig:
    """Validate and produce an updated RAGConfig by applying dot-notation overrides."""
    for key in overrides:
        _require_known_key(key)

    config_dict = base_config.model_dump()
    for key, value in overrides.items():
        parts = key.split(".")
        target = config_dict
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value

    return RAGConfig.model_validate(config_dict)


def compute_config_diff(config_a: RAGConfig, config_b: RAGConfig) -> dict[str, tuple[Any, Any]]:
    """Compute dictionary diff between two RAG configurations."""
    dict_a = config_a.model_dump()
    dict_b = config_b.model_dump()
    diffs: dict[str, tuple[Any, Any]] = {}

    def _compare(prefix: str, d1: dict, d2: dict) -> None:
        all_keys = set(d1.keys()).union(set(d2.keys()))
        for k in all_keys:
            full_key = f"{prefix}.{k}" if prefix else k
            v1 = d1.get(k)
            v2 = d2.get(k)
            if isinstance(v1, dict) and isinstance(v2, dict):
                _compare(full_key, v1, v2)
            elif v1 != v2:
                diffs[full_key] = (v1, v2)

    _compare("", dict_a, dict_b)
    return diffs

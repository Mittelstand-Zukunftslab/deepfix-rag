"""Safe YAML mutation logic and configuration diff generation."""

from typing import Any

from deepfix_rag.config.schema import RAGConfig


def apply_mutation(base_config: RAGConfig, overrides: dict[str, Any]) -> RAGConfig:
    """Validate and produce an updated RAGConfig instance by applying dot-notation overrides."""
    config_dict = base_config.model_dump()

    for key, value in overrides.items():
        parts = key.split(".")
        target = config_dict
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
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

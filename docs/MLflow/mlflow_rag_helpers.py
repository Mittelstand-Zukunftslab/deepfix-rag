from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


REQUIRED_CONFIG_SECTIONS = (
    "chunking",
    "embedding",
    "vector_store",
    "query_rewriting",
    "retrieval",
    "reranking",
    "context_assembly",
    "generation",
    "prompts",
)

REQUIRED_SECTION_KEYS = {
    "chunking": (
        "strategy",
        "chunk_size",
        "chunk_overlap",
        "parameters",
    ),
    "embedding": (
        "provider",
        "model",
        "parameters",
    ),
    "vector_store": (
        "type",
        "parameters",
    ),
    "query_rewriting": (
        "enabled",
        "strategy",
        "provider",
        "model",
        "parameters",
    ),
    "retrieval": (
        "strategy",
        "top_k",
        "parameters",
    ),
    "reranking": (
        "enabled",
        "type",
        "model",
        "top_n",
        "parameters",
    ),
    "context_assembly": (
        "strategy",
        "max_chunks",
        "parameters",
    ),
    "generation": (
        "provider",
        "model",
        "parameters",
    ),
}

PROMPT_URI_PATTERN = re.compile(r"^prompts:/[^/]+/\d+$")


def _validate_rag_config(manifest: Mapping[str, Any]) -> None:
    """
    Validate that the manifest contains a complete explicit RAG configuration.

    No defaults are filled in and no values are inferred.
    """
    if "rag_type" not in manifest:
        raise ValueError("Missing required field: rag_type")

    if not isinstance(manifest["rag_type"], str) or not manifest["rag_type"]:
        raise ValueError("rag_type must be a non-empty string")

    if "configuration" not in manifest:
        raise ValueError("Missing required field: configuration")

    configuration = manifest["configuration"]

    if not isinstance(configuration, Mapping):
        raise TypeError("configuration must be an object")

    missing_sections = [
        section
        for section in REQUIRED_CONFIG_SECTIONS
        if section not in configuration
    ]

    if missing_sections:
        raise ValueError(
            "Missing required configuration sections: "
            + ", ".join(missing_sections)
        )

    for section, required_keys in REQUIRED_SECTION_KEYS.items():
        section_config = configuration[section]

        if not isinstance(section_config, Mapping):
            raise TypeError(f"configuration.{section} must be an object")

        missing_keys = [
            key
            for key in required_keys
            if key not in section_config
        ]

        if missing_keys:
            raise ValueError(
                f"Missing required fields in configuration.{section}: "
                + ", ".join(missing_keys)
            )

    query_rewriting = configuration["query_rewriting"]

    if not isinstance(query_rewriting["enabled"], bool):
        raise TypeError(
            "configuration.query_rewriting.enabled must be a boolean"
        )

    if query_rewriting["enabled"]:
        if (
            not isinstance(query_rewriting["strategy"], str)
            or not query_rewriting["strategy"]
        ):
            raise ValueError(
                "configuration.query_rewriting.strategy must be a "
                "non-empty string when query rewriting is enabled"
            )
    else:
        for field in ("strategy", "provider", "model"):
            if query_rewriting[field] is not None:
                raise ValueError(
                    f"configuration.query_rewriting.{field} must be null "
                    "when query rewriting is disabled"
                )

    prompts = configuration["prompts"]

    if not isinstance(prompts, Mapping):
        raise TypeError("configuration.prompts must be an object")

    required_prompts = ("system", "query_rewrite")

    missing_prompts = [
        prompt
        for prompt in required_prompts
        if prompt not in prompts
    ]

    if missing_prompts:
        raise ValueError(
            "Missing required prompt fields: "
            + ", ".join(missing_prompts)
        )

    system_prompt = prompts["system"]

    if (
        not isinstance(system_prompt, str)
        or not PROMPT_URI_PATTERN.fullmatch(system_prompt)
    ):
        raise ValueError(
            "configuration.prompts.system must reference an exact "
            "MLflow Prompt Registry version"
        )

    query_rewrite_prompt = prompts["query_rewrite"]

    if query_rewrite_prompt is not None:
        if (
            not isinstance(query_rewrite_prompt, str)
            or not PROMPT_URI_PATTERN.fullmatch(query_rewrite_prompt)
        ):
            raise ValueError(
                "configuration.prompts.query_rewrite must be null or "
                "reference an exact MLflow Prompt Registry version"
            )


def _normalize_json(value: Any, path: str = "$") -> Any:
    """
    Convert a value into a deterministic JSON-compatible representation.

    Dict keys are sorted.
    List order is preserved.
    NaN, Infinity and unsupported Python objects are rejected.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"{path}: NaN and Infinity are not allowed"
            )
        return value

    if isinstance(value, Mapping):
        normalized = {}

        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError(
                    f"{path}: all object keys must be strings"
                )

            normalized[key] = _normalize_json(
                value[key],
                f"{path}.{key}",
            )

        return normalized

    if isinstance(value, (list, tuple)):
        return [
            _normalize_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    raise TypeError(
        f"{path}: unsupported value type {type(value).__name__}"
    )


def canonicalize_rag_config(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Return the complete behavioral RAG configuration in canonical form.

    Included in the hash:
        rag_type
        the entire configuration object, including query rewriting
        and exact Prompt Registry version references

    Manifest metadata outside `configuration` is excluded.
    """
    _validate_rag_config(manifest)

    behavioral_config = {
        "rag_type": deepcopy(manifest["rag_type"]),
        "configuration": deepcopy(manifest["configuration"]),
    }

    return _normalize_json(behavioral_config)


def compute_config_hash(
    manifest: Mapping[str, Any],
) -> str:
    """
    Compute SHA-256 of the complete behavioral RAG configuration.

    Returns:
        sha256:<64 lowercase hexadecimal characters>
    """
    canonical_config = canonicalize_rag_config(manifest)

    canonical_json = json.dumps(
        canonical_config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    digest = hashlib.sha256(canonical_json).hexdigest()

    return f"sha256:{digest}"
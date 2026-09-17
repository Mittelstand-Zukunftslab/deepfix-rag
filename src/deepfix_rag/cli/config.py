"""deepfix mutate-config and diff-config subcommands."""

from pathlib import Path
from typing import Any

import typer
import yaml

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_json,
    print_success,
)
from deepfix_rag.cli.utils import load_rag_config
from deepfix_rag.config.mutator import apply_mutation, compute_config_diff


def _parse_value(raw: str) -> Any:
    """Parse string override value into appropriate python scalar."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.lower() in ("none", "null"):
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def mutate_config_cmd(
    base: Path = typer.Option(..., "--base", "-b", help="Base RAG configuration YAML file."),
    set_overrides: list[str] = typer.Option(
        [],
        "--set",
        "-s",
        help="Key-value mutation override pairs in dot-notation (e.g. chunking.size=512).",
    ),
    output: Path = typer.Option(
        ..., "--output", "-o", help="Target path for mutated configuration YAML."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Validate and apply parameter overrides to a RAG config YAML."""
    if not base.exists():
        emit_error(
            f"Base config file not found: {base}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        base_config = load_rag_config(base)
    except Exception as e:
        emit_error(
            f"Failed to parse base config '{base}': {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    overrides = {}
    for item in set_overrides:
        if "=" not in item:
            emit_error(
                f"Invalid override format '{item}'. Expected format: key.subkey=value",
                code=EXIT_VALIDATION_ERROR,
                json_output=json_output,
            )
        key, val = item.split("=", 1)
        overrides[key.strip()] = _parse_value(val.strip())

    try:
        mutated_config = apply_mutation(base_config, overrides)
    except Exception as e:
        emit_error(
            f"Configuration mutation validation failed: {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            yaml.safe_dump(mutated_config.model_dump(mode="json"), f, sort_keys=False)
    except Exception as e:
        emit_error(
            f"Failed to write mutated config to '{output}': {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    diffs = compute_config_diff(base_config, mutated_config)
    result = {
        "status": "success",
        "output_path": str(output),
        "applied_overrides": overrides,
        "diffs": {k: {"before": v[0], "after": v[1]} for k, v in diffs.items()},
    }

    if json_output:
        print_json(result)
    else:
        print_success(f"Mutated configuration successfully saved to '{output}'.")
        for k, v in diffs.items():
            print(f"  - {k}: {v[0]} -> {v[1]}")


def diff_config_cmd(
    config_a: Path | None = typer.Argument(None, help="Path to first RAG configuration YAML."),
    config_b: Path | None = typer.Argument(None, help="Path to second RAG configuration YAML."),
    base: Path | None = typer.Option(
        None, "--base", "-b", help="Path to base RAG configuration YAML."
    ),
    compare: Path | None = typer.Option(
        None, "--compare", "-c", help="Path to compare RAG configuration YAML."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output diff in JSON format."),
) -> None:
    """Compare two RAG configuration YAMLs and output structural diff."""
    first = config_a or base
    second = config_b or compare
    if not first or not second:
        emit_error(
            "Both base and compare configs must be provided.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    if not first.exists():
        emit_error(
            f"Config file not found: {first}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )
    if not second.exists():
        emit_error(
            f"Config file not found: {second}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        cfg_a = load_rag_config(first)
        cfg_b = load_rag_config(second)
    except Exception as e:
        emit_error(
            f"Error loading configs for comparison: {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    diffs = compute_config_diff(cfg_a, cfg_b)
    result = {
        "status": "success",
        "config_a": str(first),
        "config_b": str(second),
        "diffs": {k: {"config_a": v[0], "config_b": v[1]} for k, v in diffs.items()},
    }

    if json_output:
        print_json(result)
    else:
        if not diffs:
            print_success("Configurations are identical.")
        else:
            print_success(f"Found {len(diffs)} differences:")
            for k, v in diffs.items():
                print(f"  - {k}: {v[0]} (config_a) vs {v[1]} (config_b)")

"""deepfix mutate-config and diff-config subcommands."""

from pathlib import Path
from typing import Any

import typer
import yaml

from deepfix_rag.cli.ui import print_error, print_json, print_success
from deepfix_rag.config.mutator import apply_mutation, compute_config_diff
from deepfix_rag.config.schema import RAGConfig


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


def _load_rag_config(config_path: Path) -> RAGConfig:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RAGConfig.model_validate(data)


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
        print_error(f"Base config file not found: {base}")
        raise typer.Exit(code=1)

    try:
        base_config = _load_rag_config(base)
    except Exception as e:
        print_error(f"Failed to parse base config '{base}': {e}")
        raise typer.Exit(code=1)

    overrides = {}
    for item in set_overrides:
        if "=" not in item:
            print_error(f"Invalid override format '{item}'. Expected format: key.subkey=value")
            raise typer.Exit(code=1)
        key, val = item.split("=", 1)
        overrides[key.strip()] = _parse_value(val.strip())

    try:
        mutated_config = apply_mutation(base_config, overrides)
    except Exception as e:
        print_error(f"Configuration mutation validation failed: {e}")
        raise typer.Exit(code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        yaml.safe_dump(mutated_config.model_dump(mode="json"), f, sort_keys=False)

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
        print_error("Both base and compare configs must be provided.")
        raise typer.Exit(code=2)

    if not first.exists():
        print_error(f"Config file not found: {first}")
        raise typer.Exit(code=1)
    if not second.exists():
        print_error(f"Config file not found: {second}")
        raise typer.Exit(code=1)

    try:
        cfg_a = _load_rag_config(first)
        cfg_b = _load_rag_config(second)
    except Exception as e:
        print_error(f"Error loading configs for comparison: {e}")
        raise typer.Exit(code=1)

    diffs = compute_config_diff(cfg_a, cfg_b)
    result = {
        "config_a": str(config_a),
        "config_b": str(config_b),
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

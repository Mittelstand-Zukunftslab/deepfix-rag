"""deepfix mutate-config and diff-config subcommands."""

from pathlib import Path

import typer

from deepfix_rag.cli.ui import print_error, print_json, print_success


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
    
    raise NotImplementedError("Not implemented yet")


def diff_config_cmd(
    config_a: Path = typer.Argument(..., help="Path to first RAG configuration YAML."),
    config_b: Path = typer.Argument(..., help="Path to second RAG configuration YAML."),
    json_output: bool = typer.Option(False, "--json", help="Output diff in JSON format."),
) -> None:
    """Compare two RAG configuration YAMLs and output structural diff."""

    raise NotImplementedError("Not implemented yet")

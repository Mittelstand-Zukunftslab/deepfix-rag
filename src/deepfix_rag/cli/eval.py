"""deepfix eval subcommand."""

from pathlib import Path

import typer

from deepfix_rag.cli.ui import print_error, print_json, print_success


def eval_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", "-d", help="Path or preset identifier for the evaluation dataset."
    ),
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    compare: Path | None = typer.Option(
        None, "--compare", help="Optional baseline configuration YAML for delta comparison."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Limit number of queries to evaluate."
    ),
) -> None:
    """Run DeepEval evaluation on a dataset using a given RAG configuration."""
    
    raise NotImplementedError("Not implemented yet")

"""deepfix index subcommand."""

from pathlib import Path

import typer

from deepfix_rag.cli.ui import print_error, print_json, print_success


def index_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", "-d", help="Path to corpus documents or dataset preset."
    ),
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    index_dir: Path = typer.Option(
        ..., "--index-dir", "-o", help="Target directory for generated index."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing index directory if present."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Build vector/lexical index for a dataset using a given configuration."""
    
    raise NotImplementedError("Not implemented yet")

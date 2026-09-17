"""deepfix export subcommand."""

from pathlib import Path

import typer

from deepfix_rag.cli.ui import EXIT_RUNTIME_ERROR, emit_error


def export_cmd(
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to best/target RAG configuration YAML file."
    ),
    output: Path = typer.Option(
        ..., "--output", "-o", help="Target output path for standalone configuration artifact."
    ),
    include_meta: bool = typer.Option(
        True, "--include-meta", help="Include run lineage and evaluation metric metadata."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Export optimized RAG configuration bundle."""
    emit_error(
        "export is not implemented yet.",
        code=EXIT_RUNTIME_ERROR,
        json_output=json_output,
    )

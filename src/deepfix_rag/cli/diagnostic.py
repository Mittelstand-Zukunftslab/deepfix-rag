"""deepfix run-diagnostic subcommand."""

from enum import Enum
from pathlib import Path

import typer

from deepfix_rag.cli.ui import EXIT_RUNTIME_ERROR, emit_error


class DiagnosticType(str, Enum):
    retrieval = "retrieval"
    entity = "entity"
    ranking = "ranking"
    grounding = "grounding"


def diagnostic_cmd(
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path or preset identifier for the evaluation dataset (overrides config.data.path_or_repo).",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    type: DiagnosticType = typer.Option(
        DiagnosticType.retrieval,
        "--type",
        "-t",
        help="Diagnostic failure mode type to analyze.",
    ),
    threshold: float = typer.Option(
        0.7, "--threshold", help="Metric score threshold under which queries are flagged."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Run deterministic failure mode diagnostics on retrieval/grounding."""
    emit_error(
        "run-diagnostic is not implemented yet.",
        code=EXIT_RUNTIME_ERROR,
        json_output=json_output,
    )

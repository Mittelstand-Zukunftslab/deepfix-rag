"""deepfix run-diagnostic subcommand."""

from enum import Enum
from pathlib import Path

import typer


class DiagnosticType(str, Enum):
    retrieval = "retrieval"
    entity = "entity"
    ranking = "ranking"
    grounding = "grounding"


def diagnostic_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", "-d", help="Path or preset identifier for the evaluation dataset."
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


    raise NotImplementedError("Not implemented yet")

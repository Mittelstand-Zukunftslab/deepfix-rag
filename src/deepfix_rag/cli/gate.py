"""deepfix gate subcommand."""

import json
from pathlib import Path
from typing import Any

import typer

from deepfix_rag.cli.ui import EXIT_VALIDATION_ERROR, emit_error, print_json, print_success
from deepfix_rag.gate import GateInputError, judge_compare


def gate_cmd(
    compare: Path = typer.Option(
        ...,
        "--compare",
        "-c",
        help="Path to eval --compare JSON.",
    ),
    primary: str = typer.Option(
        "ndcg",
        "--primary",
        "-p",
        help="Primary metric. One of ndcg, recall, precision, or a generation metric.",
    ),
    min_delta: float | None = typer.Option(
        None,
        "--min-delta",
        help="Minimum primary delta. Defaults to OPTIMIZATION_MIN_PRIMARY_DELTA.",
    ),
    secondary_tolerance: float | None = typer.Option(
        None,
        "--secondary-tolerance",
        help="How far a secondary metric may fall. Defaults to OPTIMIZATION_SECONDARY_TOLERANCE.",
    ),
    seed: int = typer.Option(0, "--seed", help="Seed for the 1000-sample bootstrap."),
    json_output: bool = typer.Option(False, "--json", help="Output the decision as JSON."),
) -> None:
    """Decide keep or revert from paired per-query eval --compare results."""
    if not compare.is_file():
        emit_error(
            f"Compare file not found: {compare}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
            error_type="missing_file",
        )
    try:
        loaded: Any = json.loads(compare.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        emit_error(
            f"Compare file is not JSON: {exc}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
            error_type="invalid_compare",
        )
    if not isinstance(loaded, dict):
        emit_error(
            "Compare file must be a JSON object.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
            error_type="invalid_compare",
        )
    try:
        decision = judge_compare(
            loaded,
            primary,
            min_delta=min_delta,
            secondary_tolerance=secondary_tolerance,
            seed=seed,
        )
    except GateInputError as exc:
        emit_error(
            str(exc),
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
            error_type="invalid_compare",
        )
    if json_output:
        print_json(decision)
        return
    verdict = "KEEP" if decision["pass"] else "REVERT"
    print_success(f"Gate {verdict} on {primary}: delta {decision['primary_delta']:.4f}.")
    for reason in decision["reasons"]:
        typer.echo(reason)
    print_json(decision)

"""Rich console output formatting, exit codes, and JSON serializers."""

import json
import sys
from typing import Any

import typer

# Standardized CLI Exit Codes per PRD FR-4
EXIT_SUCCESS = 0
EXIT_VALIDATION_ERROR = 1
EXIT_RUNTIME_ERROR = 2


def format_json(data: dict[str, Any], pretty: bool = True) -> str:
    """Serialize dictionary payload to formatted JSON string."""
    if pretty:
        return json.dumps(data, indent=2, default=str)
    return json.dumps(data, default=str)


def print_json(data: dict[str, Any]) -> None:
    """Print structured JSON output to stdout."""
    typer.echo(format_json(data), file=sys.stdout)


def print_info(message: str) -> None:
    """Print informational message to stderr to preserve clean stdout."""
    typer.secho(message, err=True)


def print_error(message: str) -> None:
    """Print error message to stderr."""
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)


def print_success(message: str) -> None:
    """Print success message to stdout."""
    typer.secho(message, fg=typer.colors.GREEN)


def emit_error(
    message: str,
    code: int = EXIT_VALIDATION_ERROR,
    json_output: bool = False,
) -> None:
    """Emit error according to format preference and exit with designated exit code."""
    if json_output:
        print_json({"status": "error", "error": message, "code": code})
    else:
        typer.secho(f"Error: {message}", fg=typer.colors.RED, file=sys.stdout)
    print_error(message)
    raise typer.Exit(code=code)

"""Rich console output formatting and JSON serializers."""

import json
from typing import Any

import typer


def format_json(data: dict[str, Any], pretty: bool = True) -> str:
    """Serialize dictionary payload to formatted JSON string."""
    if pretty:
        return json.dumps(data, indent=2, default=str)
    return json.dumps(data, default=str)


def print_json(data: dict[str, Any]) -> None:
    """Print structured JSON output to stdout."""
    typer.echo(format_json(data))


def print_error(message: str) -> None:
    """Print error message to stderr."""
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)


def print_success(message: str) -> None:
    """Print success message to stdout."""
    typer.secho(message, fg=typer.colors.GREEN)

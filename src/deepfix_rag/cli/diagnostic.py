"""deepfix run-diagnostic subcommand."""

import contextlib
from pathlib import Path
import sys

from deepfix_core.models.api import APIResponse
import typer

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_ERROR,
    emit_error,
)
from deepfix_rag.diagnostics.engine import run_diagnostics


def diagnostic_cmd(
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path or preset identifier for the evaluation dataset.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to RAG configuration YAML file.",
    ),
    server_url: str | None = typer.Option(
        None,
        "--server-url",
        help="URL of the DeepFix analysis server (defaults to DEEPFIX_SERVER_URL or http://localhost:8844).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Max number of queries to sample and diagnose.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results in JSON format.",
    ),
) -> None:
    """Run deterministic failure mode diagnostics on retrieval/grounding via DeepFixClient."""
    if config is not None and not config.exists():
        emit_error(
            f"Config file not found: {config}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        if json_output:
            with contextlib.redirect_stdout(sys.stderr):
                response: APIResponse = run_diagnostics(
                    dataset=dataset,
                    config=config,
                    server_url=server_url,
                    limit=limit,
                )
        else:
            response: APIResponse = run_diagnostics(
                dataset=dataset,
                config=config,
                server_url=server_url,
                limit=limit,
            )
    except ConnectionError as err:
        emit_error(str(err), code=EXIT_RUNTIME_ERROR, json_output=json_output)
    except (ValueError, FileNotFoundError) as err:
        emit_error(str(err), code=EXIT_VALIDATION_ERROR, json_output=json_output)
    except Exception as err:
        emit_error(
            f"Diagnostics runtime execution failed: {err}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    if json_output:
        typer.echo(response.model_dump_json(indent=2), file=sys.stdout)
    else:
        text_report = response.get_results_as_text()
        typer.echo(text_report)

    raise typer.Exit(code=EXIT_SUCCESS)

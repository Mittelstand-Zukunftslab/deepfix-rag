"""deepfix schema subcommand."""

from typing import Any

import typer

from deepfix_rag.cli.ui import print_json
from deepfix_rag.config.schema import CONFIG_SCHEMA_VERSION, RAGConfig
from deepfix_rag.index_manifest import index_time_key_names, index_time_signature


def schema_payload() -> dict[str, Any]:
    """Fields, bounds, enums, and the index-time key list."""
    config = RAGConfig()
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "index_time_keys": index_time_key_names(),
        "index_time_signature_keys": list(index_time_signature(config)),
        "json_schema": RAGConfig.model_json_schema(),
    }


def schema_cmd(
    json_output: bool = typer.Option(False, "--json", help="Print the RAGConfig schema as JSON."),
) -> None:
    """Print the RAG configuration schema and the keys that require a new index."""
    payload = schema_payload()
    if json_output:
        print_json(payload)
        return
    typer.echo(f"schema_version: {payload['schema_version']}")
    typer.echo("index_time_keys:")
    for key in payload["index_time_keys"]:
        typer.echo(f"  - {key}")
    properties = payload["json_schema"].get("properties", {})
    typer.echo(f"top_level_fields: {', '.join(properties)}")

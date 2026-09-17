"""deepfix index subcommand."""

import asyncio
from pathlib import Path
import shutil

import typer
import yaml

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_info,
    print_json,
    print_success,
)
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.data import DataLoaderConfig
from deepfix_rag.pipeline import RAGWorkflow


def _load_rag_config(config_path: Path) -> RAGConfig:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RAGConfig.model_validate(data)


def index_cmd(
    dataset: str = typer.Option(
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
    try:
        rag_config = _load_rag_config(config)
    except FileNotFoundError:
        emit_error(
            f"Config file not found: {config}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )
    except Exception as e:
        emit_error(
            f"Failed to parse config '{config}': {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    if index_dir.exists() and any(index_dir.iterdir()):
        if not force:
            emit_error(
                f"Index directory '{index_dir}' already exists and is not empty. Use --force to overwrite.",
                code=EXIT_VALIDATION_ERROR,
                json_output=json_output,
            )
        else:
            shutil.rmtree(index_dir)
            index_dir.mkdir(parents=True, exist_ok=True)
    else:
        index_dir.mkdir(parents=True, exist_ok=True)

    try:
        rag_config.retrieval.lancedb_index_dir = str(index_dir)
        rag_config.data = DataLoaderConfig(path_or_repo=dataset)

        workflow = RAGWorkflow(config=rag_config)

        print_info("Triggering asynchronous RAGWorkflow data ingestion...")

        async def _run_ingest():
            return await workflow.run(ingest=True)

        asyncio.run(_run_ingest())

    except typer.Exit:
        raise
    except Exception as e:
        emit_error(
            f"Workflow Indexing failed: {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    if json_output:
        print_json({"status": "success", "index_path": str(index_dir)})
    else:
        print_success(f"Indexed documents successfully via Workflow into LanceDB at {index_dir}.")

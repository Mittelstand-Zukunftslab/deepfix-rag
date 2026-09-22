"""deepfix index subcommand."""

import asyncio
from pathlib import Path
import shutil

import typer

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_info,
    print_json,
    print_success,
)
from deepfix_rag.cli.utils import load_rag_config
from deepfix_rag.config.settings import settings
from deepfix_rag.data import DataLoaderConfig
from deepfix_rag.pipeline import RAGWorkflow


def index_cmd(
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path to corpus documents or dataset preset (overrides config.data.path_or_repo).",
    ),
    index_dir: Path | None = typer.Option(
        None,
        "--index-dir",
        "-o",
        help="Target directory for generated index (overrides config.retrieval.lancedb_index_dir).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing index directory if present."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Build vector/lexical index for a dataset using a given configuration."""
    try:
        rag_config = load_rag_config(config)
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

    target_dataset = dataset or (rag_config.data.path_or_repo if rag_config.data else None)
    if not target_dataset:
        emit_error(
            "Dataset must be specified either via --dataset flag or within config.data.path_or_repo.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    target_index_dir = (
        str(index_dir)
        if index_dir is not None
        else (rag_config.retrieval.lancedb_index_dir if rag_config.retrieval else None)
    )
    if not target_index_dir:
        emit_error(
            "Index directory must be specified either via --index-dir flag or within config.retrieval.lancedb_index_dir.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    if not str(target_index_dir).startswith("s3://"):
        local_path = Path(target_index_dir)
        if local_path.exists() and any(local_path.iterdir()):
            if not force:
                emit_error(
                    f"Index directory '{target_index_dir}' already exists and is not empty. Use --force to overwrite.",
                    code=EXIT_VALIDATION_ERROR,
                    json_output=json_output,
                )
            else:
                shutil.rmtree(local_path)
                local_path.mkdir(parents=True, exist_ok=True)
        else:
            local_path.mkdir(parents=True, exist_ok=True)

    try:
        rag_config.retrieval.lancedb_index_dir = str(target_index_dir)
        rag_config.data = DataLoaderConfig(path_or_repo=target_dataset)

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

    metadata = workflow.last_build_metadata
    if json_output:
        payload: dict = {
            "status": "success",
            "index_path": str(target_index_dir),
            "paradigm": rag_config.retrieval.backend.value,
            "embedding_model": settings.EMBEDDING_MODEL,
            "reranker_model": settings.COHERE_MODEL,
        }
        if metadata is not None:
            payload.update(metadata.model_dump(mode="json"))
        print_json(payload)
    else:
        print_success(
            f"Indexed documents successfully via Workflow into LanceDB at {target_index_dir}."
        )

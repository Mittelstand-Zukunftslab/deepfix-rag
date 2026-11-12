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
from deepfix_rag.data import with_dataset_path
from deepfix_rag.index_manifest import manifest_matches, read_index_manifest
from deepfix_rag.pipeline import RAGWorkflow, index_workflow_timeout
from deepfix_rag.run_bundle import (
    RunBundleError,
    S3ObjectStore,
    clear_index_prefix,
)


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
    index_dir: str | None = typer.Option(
        None,
        "--index-dir",
        "-o",
        help=(
            "Target directory or s3:// URI for the generated index "
            "(overrides config.retrieval.lancedb_index_dir)."
        ),
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing index directory if present."
    ),
    if_changed: bool = typer.Option(
        False,
        "--if-changed",
        help="Skip the build when the index manifest matches this config. Rebuild when it does not.",
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

    # Keep this a string. pathlib.Path collapses s3://bucket/key to s3:/bucket/key.
    target_index_dir = (
        index_dir
        if index_dir is not None
        else (rag_config.retrieval.lancedb_index_dir if rag_config.retrieval else None)
    )
    if not target_index_dir:
        emit_error(
            "Index directory must be specified either via --index-dir flag or within config.retrieval.lancedb_index_dir.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    rag_config.retrieval.lancedb_index_dir = str(target_index_dir)
    rag_config.data = with_dataset_path(rag_config.data, target_dataset)

    if not str(target_index_dir).startswith("s3://") and manifest_matches(
        target_index_dir, rag_config
    ):
        if if_changed:
            manifest = read_index_manifest(target_index_dir) or {}
            payload = {
                "status": "success",
                "skipped": True,
                "index_path": str(target_index_dir),
                "signature_hash": manifest.get("signature_hash"),
                "paradigm": rag_config.retrieval.backend.value,
                "embedding_model": settings.EMBEDDING_MODEL,
                "reranker_model": settings.COHERE_MODEL,
            }
            if json_output:
                print_json(payload)
            else:
                print_success(f"Index at {target_index_dir} is unchanged. Skipped the rebuild.")
            return

    if not str(target_index_dir).startswith("s3://"):
        local_path = Path(target_index_dir)
        if local_path.exists() and any(local_path.iterdir()):
            if not force and not if_changed:
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
    elif force:
        _clear_remote_index(str(target_index_dir), json_output=json_output)

    try:
        workflow = RAGWorkflow(config=rag_config, timeout=index_workflow_timeout())

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
        written = read_index_manifest(target_index_dir) or {}
        payload: dict = {
            "status": "success",
            "skipped": False,
            "index_path": str(target_index_dir),
            "signature_hash": written.get("signature_hash"),
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


def _clear_remote_index(uri: str, *, json_output: bool) -> None:
    """Delete objects under this index URI before a forced rebuild.

    The prefix comes from the URI itself and refuses an empty path, so a
    force rebuild of ``s3://bucket/dev`` cannot wipe the rest of the bucket.
    """
    if not settings.S3_ACCESS_KEY_ID or not settings.S3_SECRET_ACCESS_KEY:
        emit_error(
            "S3 credentials are not configured; cannot clear the remote index.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )
    store = S3ObjectStore(
        endpoint_url=settings.S3_ENDPOINT_URL,
        access_key_id=settings.S3_ACCESS_KEY_ID,
        secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region=settings.S3_REGION,
    )
    try:
        removed = clear_index_prefix(store, uri)
    except RunBundleError as exc:
        emit_error(str(exc), code=EXIT_VALIDATION_ERROR, json_output=json_output)
    except Exception:
        emit_error(
            f"Could not clear the index prefix at {uri}.",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )
    print_info(f"Removed {removed} objects under {uri}.")

"""deepfix index subcommand."""

from pathlib import Path

import typer
import yaml

from deepfix_rag.cli.ui import print_error, print_json, print_success
from deepfix_rag.config.schema import RAGConfig
import asyncio
from deepfix_rag.pipeline import RAGWorkflow
from deepfix_rag.data import DataLoaderConfig, DataSourceType


def _load_rag_config(config_path: Path) -> RAGConfig:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RAGConfig.model_validate(data)


def index_cmd(
    dataset: Path = typer.Option(
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
        print_error(f"Config file not found: {config}")
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Failed to parse config '{config}': {e}")
        raise typer.Exit(code=1)

    try:
        # Wrap the user path into our strict Pydantic scheme
        data_config = DataLoaderConfig(
            source_type=DataSourceType.DIRECTORY if dataset.is_dir() else DataSourceType.LOCAL_FILE,
            path_or_repo=str(dataset),
            text_column="text",
            doc_id_column="id"
        )
        
        # Instantiate the Workflow explicitly
        workflow = RAGWorkflow(config=rag_config, index_dir=str(index_dir))
        
        # Trigger the LlamaIndex Ingest step natively!
        print(f"Triggering asynchronous RAGWorkflow data ingestion...")
        index = asyncio.run(workflow.run(data_config=data_config))
        
    except Exception as e:
        print_error(f"Workflow Indexing failed: {e}")
        raise typer.Exit(code=1)

    if json_output:
        print_json({"status": "success", "index_path": str(index_dir)})
    else:
        print_success(f"Indexed documents successfully via Workflow into LanceDB at {index_dir}.")
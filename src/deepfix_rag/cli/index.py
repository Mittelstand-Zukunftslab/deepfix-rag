"""deepfix index subcommand."""

from pathlib import Path

import typer
import yaml

from deepfix_rag.cli.ui import print_error, print_json, print_success
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.evaluation.indexing import build_index


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
        result = build_index(
            dataset_path=dataset,
            config=rag_config,
            index_dir=str(index_dir),
            force=force,
        )
    except FileExistsError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Indexing failed: {e}")
        raise typer.Exit(code=1)

    if json_output:
        print_json(result)
    else:
        print_success(
            f"Indexed {result['documents']} documents into {result['chunks']} chunks "
            f"at {result['index_path']}."
        )
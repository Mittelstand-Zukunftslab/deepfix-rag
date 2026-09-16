import json
from pathlib import Path

from rich.console import Console
import typer

from deepfix_rag.data import DataLoader
from deepfix_rag.config.settings import settings
from deepfix_rag.config.schema import DataPreset, DataLoaderConfig, DataSourceType

app = typer.Typer(
    help="Manage and prepare RAG datasets with strict dev/test split isolation.",
    no_args_is_help=True,
)
console = Console()


@app.command("load")
def load_dataset(
    repo: str = typer.Option(
        ..., "--repo", help="HuggingFace dataset repository (e.g. wirthual/dip-bundestag-qa)"
    ),
    split: str = typer.Option("train", "--split", help="Target split (e.g., train, dev, test)"),
    text_column: str = typer.Option(
        "text", "--text-column", help="The primary text/query column map in the dataset"
    ),
):
    """Download and vectorize a dataset using the unified DataLoader."""

    console.print(f"[green]Requesting unified DataLoader for '{repo}' (split: {split})...[/green]")

    try:
        # Build the exact Pydantic Config expected by our DataLoader Interface
        config = DataLoaderConfig(
            source_type=DataSourceType.HUGGINGFACE,
            path_or_repo=repo,
            split=split,
            text_column=text_column,
        )
        documents = DataLoader().load_documents(config)

        console.print(
            f"[bold green]Successfully loaded {len(documents)} unified LlamaIndex documents![/bold green]"
        )

    except Exception as e:
        console.print(f"[red]Failed to load dataset with config: '{config}'.\nError: {e}[/red]")
        raise typer.Exit(code=1)

#TODO:
@app.command("load")
def load_dataset_cmd(
    name: str = typer.Option(..., "--name", help="Dataset preset (e.g. garage, mtrag) or custom HF path"),
    split: str = typer.Option(..., "--split", help="Target split (must be 'dev' or 'test')"),
    stream: bool = typer.Option(False, "--stream", help="Enable streaming for large datasets"),
):
    """Download and standardize a dataset into local cache with strict split isolation."""
    
    raise NotImplementedError("Not implemented yet")


@app.command("list")
def list_datasets_cmd(
    data_root: Path | None = typer.Option(None, "--data-root", help="Custom root directory for datasets"),
):
    """List all locally downloaded datasets and available splits."""
    root = data_root or Path(settings.DATA_ROOT)
    if not root.exists():
        console.print("[yellow]No local datasets found.[/yellow]")
        return

    datasets_found = []
    for d in root.iterdir():
        if d.is_dir():
            splits = [s.name for s in d.iterdir() if s.is_dir()]
            if splits:
                datasets_found.append((d.name, splits))

    if not datasets_found:
        console.print("[yellow]No local datasets found.[/yellow]")
        return

    console.print("[bold]Locally available datasets:[/bold]")
    for name, splits in sorted(datasets_found):
        console.print(f" - [cyan]{name}[/cyan]: splits = {', '.join(splits)}")

#TODO:
@app.command("generate-unanswerable")
def generate_unanswerable_cmd(
    dataset: Path = typer.Option(..., "--dataset", help="Path to dataset directory containing qa.jsonl"),
    ratio: float = typer.Option(0.2, "--ratio", help="Ratio of unanswerable queries to generate"),
    output: Path = typer.Option(..., "--output", help="Output path for synthetic QA file"),
):
    raise NotImplementedError("Not implemented yet")
    
    
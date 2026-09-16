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
    name: str = typer.Option(
        ...,
        "--name",
        "--repo",
        help="Dataset name preset (e.g. bundestag-qa) or HuggingFace repo (e.g. wirthual/dip-bundestag-qa)",
    ),
    split: str = typer.Option("train", "--split", help="Target split (e.g., train, dev, test)"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of records to load"),
    data_root: Path | None = typer.Option(
        None, "--data-root", help="Root directory for dataset cache"
    ),
):
    """Download and standardize a dataset into local cache with strict split isolation."""
    if split != "train":
        console.print(f"[red]Error: split must be 'train' (got '{split}').[/red]")
        raise typer.Exit(code=1)

    console.print(f"Loading dataset '{name}' (split: {split})...")

    try:
        loader = DataLoader()
        corpus_file, qa_file = loader.load_and_save(
            name_or_repo=name,
            split=split,
            data_root=data_root,
            limit=limit,
        )
        target_dir = corpus_file.parent
        console.print(
            f"[bold green]Dataset '{name}' isolated successfully for {split} at {target_dir}[/bold green]"
        )

    except Exception as e:
        console.print(f"[red]Failed to load dataset '{name}'.\nError: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("list")
def list_datasets_cmd(
    data_root: Path = typer.Option(
        Path(settings.DATA_PROCESSES_DIR), "--data-root", help="Custom root directory for datasets"
    ),
):
    """List all locally downloaded datasets and available splits."""
    root = data_root
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


# TODO:
@app.command("generate-unanswerable")
def generate_unanswerable_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", help="Path to dataset directory containing qa.jsonl"
    ),
    ratio: float = typer.Option(0.2, "--ratio", help="Ratio of unanswerable queries to generate"),
    output: Path = typer.Option(..., "--output", help="Output path for synthetic QA file"),
):
    raise NotImplementedError("Not implemented yet")

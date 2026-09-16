from pathlib import Path
from typing import Optional

from rich.console import Console
import typer

from deepfix_rag.data import DataLoader, DataLoaderConfig, DataSourceType

app = typer.Typer(
    help="Manage and prepare RAG datasets with strict dev/test split isolation.",
    no_args_is_help=True,
)
console = Console()

DATA_ROOT = Path("data/datasets")


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

        # Instantiate the Factory Pattern Loader
        loader = DataLoader()
        documents = loader.load_documents(config)

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
    
    pass


@app.command("list")
def list_datasets_cmd():
    """List all locally downloaded datasets and available splits."""
    if not DATA_ROOT.exists():
        console.print("[yellow]No local datasets found in data/datasets/.[/yellow]")
        return

    datasets_found = []
    for d in DATA_ROOT.iterdir():
        if d.is_dir():
            splits = [s.name for s in d.iterdir() if s.is_dir()]
            datasets_found.append((d.name, splits))

    if not datasets_found:
        console.print("[yellow]No local datasets found.[/yellow]")
        return

    console.print("[bold]Locally available datasets:[/bold]")
    for name, splits in sorted(datasets_found):
        console.print(f" - [cyan]{name}[/cyan]: splits = {', '.join(splits)}")


@app.command("generate-unanswerable")
def generate_unanswerable_cmd(
    dataset: Path = typer.Option(..., "--dataset", help="Path to dataset directory containing qa.jsonl"),
    ratio: float = typer.Option(0.2, "--ratio", help="Ratio of unanswerable queries to generate"),
    output: Path = typer.Option(..., "--output", help="Output path for synthetic QA file"),
):
    """Generate synthetic unanswerable queries to test deflection calibration."""
    console.print(f"[green]Generating synthetic unanswerable queries (ratio={ratio})...[/green]")
    
    qa_file = dataset / "qa.jsonl" if dataset.is_dir() else dataset
    if not qa_file.exists():
        console.print(f"[red]Error: Dataset file '{qa_file}' not found.[/red]")
        raise typer.Exit(code=1)

    with open(qa_file, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    n_synth = max(1, int(len(records) * ratio))
    synth_records = []
    for i in range(n_synth):
        synth_records.append({
            "query_id": f"synth_unans_{i}",
            "query": f"Synthetic unanswerable query regarding out-of-domain subject #{i}",
            "answer": "I do not have sufficient information in the provided documents to answer this question.",
            "relevant_docs": [],
            "unanswerable": True,
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for r in synth_records:
            f.write(json.dumps(r) + "\n")

    console.print(f"[bold green]Successfully generated {len(synth_records)} synthetic queries -> {output}[/bold green]")

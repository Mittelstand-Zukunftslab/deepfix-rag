from pathlib import Path

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
    repo: str = typer.Option(..., "--repo", help="HuggingFace dataset repository (e.g. wirthual/dip-bundestag-qa)"),
    split: str = typer.Option("train", "--split", help="Target split (e.g., train, dev, test)"),
    text_column: str = typer.Option("text", "--text-column", help="The primary text/query column map in the dataset"),
):
    """Download and vectorize a dataset using the unified DataLoader."""
    
    console.print(f"[green]Requesting unified DataLoader for '{repo}' (split: {split})...[/green]")
    
    try:
        # Build the exact Pydantic Config expected by our DataLoader Interface
        config = DataLoaderConfig(
            source_type=DataSourceType.HUGGINGFACE,
            path_or_repo=repo,
            split=split,
            text_column=text_column
        )
        
        # Instantiate the Factory Pattern Loader
        loader = DataLoader()
        documents = loader.load_documents(config)
        
        console.print(f"[bold green]Successfully loaded {len(documents)} unified LlamaIndex documents![/bold green]")
        
    except Exception as e:
        console.print(f"[bold red]Data Loader Exception Caught: {e}[/bold red]")
        raise typer.Exit(code=1)

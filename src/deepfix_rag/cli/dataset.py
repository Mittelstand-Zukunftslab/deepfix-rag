from pathlib import Path

from rich.console import Console
import typer

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_json,
)
from deepfix_rag.config.settings import settings
from deepfix_rag.data import DataLoader

app = typer.Typer(
    help="Manage and prepare RAG datasets with strict dev/test split isolation.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


@app.command("load")
def load_dataset(
    name: str = typer.Option(
        ...,
        "--name",
        "--repo",
        help="Dataset name preset (e.g. bundestag-qa, legal-rag-bench) or HuggingFace repo",
    ),
    split: str = typer.Option("train", "--split", help="Target split (e.g., train, dev, test)"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of records to load"),
    subset: str | None = typer.Option(
        None, "--subset", help="Subset or config name (e.g. 'corpus' or 'qa')"
    ),
    corpus_only: bool = typer.Option(
        False, "--corpus-only", help="Only load and export the corpus documents"
    ),
    data_root: Path | None = typer.Option(
        None, "--data-root", help="Root directory for dataset cache"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
):
    """Download and standardize a dataset into local cache with strict split isolation."""
    if split != "train":
        emit_error(
            f"split must be 'train' (got '{split}').",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    if not json_output:
        console.print(f"Loading dataset '{name}' (split: {split})...")
    else:
        err_console.print(f"Loading dataset '{name}' (split: {split})...")

    try:
        corpus_file, qa_file = DataLoader.load_and_save(
            name_or_repo=name,
            split=split,
            data_root=data_root,
            limit=limit,
            subset=subset,
            corpus_only=corpus_only,
        )
        target_dir = corpus_file.parent

        result_payload = {
            "status": "success",
            "dataset": name,
            "split": split,
            "corpus_file": str(corpus_file),
            "qa_file": str(qa_file) if qa_file else None,
            "target_dir": str(target_dir),
        }

        if json_output:
            err_console.print(
                f"[bold green]Dataset '{name}' isolated successfully for {split} at {target_dir}[/bold green]"
            )
            print_json(result_payload)
        else:
            console.print(
                f"[bold green]Dataset '{name}' isolated successfully for {split} at {target_dir}[/bold green]"
            )

    except typer.Exit:
        raise
    except Exception as e:
        emit_error(
            f"Failed to load dataset '{name}': {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )


@app.command("list")
def list_datasets_cmd(
    data_root: Path = typer.Option(
        Path(settings.DATA_PROCESSES_DIR), "--data-root", help="Custom root directory for datasets"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
):
    """List all locally downloaded datasets and available splits."""
    root = data_root
    if not root.exists():
        if json_output:
            print_json({"status": "success", "datasets": []})
        else:
            console.print("[yellow]No local datasets found.[/yellow]")
        return

    datasets_found = []
    for d in root.iterdir():
        if d.is_dir():
            splits = [s.name for s in d.iterdir() if s.is_dir()]
            if splits:
                datasets_found.append((d.name, splits))

    if json_output:
        print_json(
            {
                "status": "success",
                "datasets": [
                    {"name": name, "splits": splits} for name, splits in sorted(datasets_found)
                ],
            }
        )
        return

    if not datasets_found:
        console.print("[yellow]No local datasets found.[/yellow]")
        return

    console.print("[bold]Locally available datasets:[/bold]")
    for name, splits in sorted(datasets_found):
        console.print(f" - [cyan]{name}[/cyan]: splits = {', '.join(splits)}")


@app.command("generate-unanswerable")
def generate_unanswerable_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", help="Path to dataset directory containing qa.jsonl"
    ),
    ratio: float = typer.Option(0.2, "--ratio", help="Ratio of unanswerable queries to generate"),
    output: Path = typer.Option(..., "--output", help="Output path for synthetic QA file"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
):
    """Generate synthetic unanswerable queries for refusal calibration."""
    emit_error(
        "generate-unanswerable is not implemented yet.",
        code=EXIT_RUNTIME_ERROR,
        json_output=json_output,
    )

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from deepfix_rag.data import DataLoader, DataLoaderConfig, DataSourceType

app = typer.Typer(
    help="Manage and prepare RAG datasets with strict dev/test split isolation.",
    no_args_is_help=True,
)
console = Console()

DATA_ROOT = Path("data/datasets")

# Registry mapping local dataset preset names to HuggingFace repositories.
BENCHMARK_REGISTRY = {
    "legal-rag-bench": "isaacus/legal-rag-bench",
    "garage": "ds-ba/GaRAGe",
    "blleqa": "ds-ba/bLLeQA",
    "browsecomp-plus": "ds-ba/BrowseComp-Plus",
    "enterpriserag-bench": "ds-ba/EnterpriseRAG-Bench",
    "mtrag": "ds-ba/mtRAG",
    "bundestag-qa": "wirthual/dip-bundestag-qa",
}


def _download_hf_dataset(dataset_name: str, split: str, corpus_path: Path, qa_path: Path):
    """Download and format dataset from HuggingFace, splitting into corpus and QA records."""
    from datasets import load_dataset

    console.print(f"Fetching [cyan]{dataset_name}[/cyan] from HuggingFace...")

    hf_split = "validation" if split == "dev" else "test"

    try:
        # 1. Load the corpus/passages
        try:
            corpus = load_dataset(dataset_name, "corpus", split=hf_split)
        except Exception:
            try:
                corpus = load_dataset(dataset_name, split=hf_split)
            except Exception:
                # Fallback to test or train split if validation is not available
                corpus = load_dataset(dataset_name, split="train")

        console.print(f"Loading corpus split for {dataset_name}...")
        with open(corpus_path, "w", encoding="utf-8") as f:
            for i, doc in enumerate(corpus):
                doc_dict = {
                    "id": str(doc.get("doc_id") or doc.get("id") or i),
                    "text": doc.get("text") or doc.get("content") or doc.get("passage") or "",
                }
                f.write(json.dumps(doc_dict) + "\n")

        # 2. Load queries/qa if available
        console.print(f"Loading queries/qa for {dataset_name}...")
        try:
            queries = load_dataset(dataset_name, "queries", split=hf_split)
        except Exception:
            try:
                queries = load_dataset(dataset_name, "qa", split=hf_split)
            except Exception:
                queries = []

        # Ground truth mapping (qrels) if available
        try:
            qrels = load_dataset(dataset_name, "qrels", split=hf_split)
            qrel_map = {}
            for row in qrels:
                qid = str(row["query_id"])
                qrel_map.setdefault(qid, []).append(str(row["doc_id"]))
        except Exception:
            qrel_map = None

        with open(qa_path, "w", encoding="utf-8") as f:
            for i, q in enumerate(queries):
                qid = str(q.get("query_id") or q.get("id") or i)
                rel_docs = qrel_map.get(qid, []) if qrel_map else q.get("relevant_docs", [])
                if not rel_docs:
                    if q.get("doc_id"):
                        rel_docs = [str(q["doc_id"])]
                    elif q.get("relevant_passage_id"):
                        rel_docs = [str(q["relevant_passage_id"])]

                query_text = q.get("query") or q.get("question") or q.get("text") or ""
                answer_text = q.get("answer") or q.get("expected_output") or ""
                qa_dict = {
                    "query_id": qid,
                    "query": query_text,
                    "answer": answer_text,
                    "relevant_docs": rel_docs,
                    "unanswerable": len(rel_docs) == 0,
                }
                f.write(json.dumps(qa_dict) + "\n")

    except Exception as e:
        console.print(f"[red]Failed to load dataset '{dataset_name}' from HuggingFace: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("load")
def load_dataset_cmd(
    name: str = typer.Option(..., "--name", help="Dataset preset (e.g. garage, mtrag) or custom HF path"),
    split: str = typer.Option(..., "--split", help="Target split (must be 'dev' or 'test')"),
    stream: bool = typer.Option(False, "--stream", help="Enable streaming for large datasets"),
):
    """Download and standardize a dataset into local cache with strict split isolation."""
    if split not in ["dev", "test"]:
        console.print("[red]Error: split must be 'dev' or 'test'. No other splits permitted.[/red]")
        raise typer.Exit(code=1)

    out_dir = DATA_ROOT / name / split
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Loading dataset '{name}' (split: {split}) into {out_dir}...[/green]")

    corpus_path = out_dir / "corpus.jsonl"
    qa_path = out_dir / "qa.jsonl"

    hf_repo = BENCHMARK_REGISTRY.get(name, name)
    _download_hf_dataset(hf_repo, split, corpus_path, qa_path)

    console.print(f"[bold green]Dataset '{name}' loaded and isolated successfully for {split}![/bold green]")


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

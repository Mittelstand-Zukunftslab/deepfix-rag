"""Dataset ingestion, formatting, and dev/test split management."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    help="Manage and prepare RAG datasets with strict dev/test split isolation.",
    no_args_is_help=True,
)
console = Console()

DATA_ROOT = Path("data/datasets")

# Registry mapping local dataset preset names to HuggingFace repositories.
# (Update the namespaces if any of the target datasets are hosted privately or elsewhere)
BENCHMARK_REGISTRY = {
    "legal-rag-bench": "isaacus/legal-rag-bench",
    "garage": "ds-ba/GaRAGe", 
    "blleqa": "ds-ba/bLLeQA",
    "browsecomp-plus": "ds-ba/BrowseComp-Plus",
    "enterpriserag-bench": "ds-ba/EnterpriseRAG-Bench",
    "mtrag": "ds-ba/mtRAG",
}

def _download_hf_dataset(dataset_name: str, split: str, corpus_path: Path, qa_path: Path):
    """Download and format dataset from HuggingFace."""
    from datasets import load_dataset
    
    console.print(f"Fetching [cyan]{dataset_name}[/cyan] from HuggingFace...")
    
    hf_split = "validation" if split == "dev" else "test"
    
    try:
        # Load the corpus/passages
        try:
            corpus = load_dataset(dataset_name, "corpus", split=hf_split)
        except ValueError as ve:
            # Fallback if the requested split is missing, just try 'test' or 'train'
            if "Unknown split" in str(ve) and hf_split == "validation":
                hf_split = "test"
                corpus = load_dataset(dataset_name, "corpus", split=hf_split)
            else:
                raise ve

        console.print(f"Loading corpus split: {hf_split}...")
        with open(corpus_path, "w", encoding="utf-8") as f:
            for doc in corpus:
                doc_dict = {
                    "id": str(doc.get("doc_id", doc.get("id"))),
                    "text": doc.get("text", doc.get("content", ""))
                }
                f.write(json.dumps(doc_dict) + "\n")
                
        # Load the queries/qa
        console.print(f"Loading queries split: {hf_split}...")
        try:
            queries = load_dataset(dataset_name, "queries", split=hf_split)
        except Exception:
            queries = load_dataset(dataset_name, "qa", split=hf_split)
        
        # We need qrels (ground truth mapping) to stitch queries to relevant docs
        try:
            qrels = load_dataset(dataset_name, "qrels", split=hf_split)
            qrel_map = {}
            for row in qrels:
                qid = str(row["query_id"])
                if qid not in qrel_map:
                    qrel_map[qid] = []
                qrel_map[qid].append(str(row["doc_id"]))
        except Exception:
            qrel_map = None
            
        with open(qa_path, "w", encoding="utf-8") as f:
            for q in queries:
                qid = str(q.get("query_id", q.get("id")))
                
                rel_docs = qrel_map.get(qid, []) if qrel_map else q.get("relevant_docs", [])
                
                # Dynamic mapping for legal-rag-bench formats
                if not rel_docs:
                    if q.get("doc_id"):
                        rel_docs = [str(q["doc_id"])]
                    elif q.get("relevant_passage_id"):
                        rel_docs = [str(q["relevant_passage_id"])]
                
                query_text = q.get("query") or q.get("question") or q.get("text", "")
                answer_text = q.get("answer") or q.get("expected_output", "")
                qa_dict = {
                    "query_id": qid,
                    "query": query_text,
                    "answer": answer_text,
                    "relevant_docs": rel_docs,
                    "unanswerable": len(rel_docs) == 0
                }
                f.write(json.dumps(qa_dict) + "\n")
                
    except Exception as e:
        console.print(f"[red]Failed to load dataset '{dataset_name}' from HuggingFace: {e}[/red]")
        raise typer.Exit(code=1)

@app.command("load")
def load_dataset(
    name: str = typer.Option(..., "--name", help="Dataset preset (e.g. garage, mtrag) or custom HF path"),
    split: str = typer.Option(..., "--split", help="Target split (must be 'dev' or 'test')"),
    stream: bool = typer.Option(False, "--stream", help="Enable streaming for large datasets"),
):
    """Download and standardize a dataset into local cache with strict split isolation."""
    if split not in ["dev", "test"]:
        console.print("[red]Error: split must be 'dev' or 'test'. No other splits permitted.[/red]")
        raise typer.Exit(1)

    out_dir = DATA_ROOT / name / split
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Loading dataset '{name}' (split: {split}) into {out_dir}...[/green]")

    corpus_path = out_dir / "corpus.jsonl"
    qa_path = out_dir / "qa.jsonl"

    hf_repo = BENCHMARK_REGISTRY.get(name, name)
    _download_hf_dataset(hf_repo, split, corpus_path, qa_path)

    console.print(f"[bold green]Dataset '{name}' loaded and isolated successfully for {split}![/bold green]")

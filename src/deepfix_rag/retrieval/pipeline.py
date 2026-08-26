"""RAG retrieval execution wrapper using LanceDB with fallback."""

import json
import math
from pathlib import Path

import lancedb
from pydantic import BaseModel, Field

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.retrieval.models import EmbeddingClient


class RetrievalResult(BaseModel):
    """Retrieved document candidate."""
    doc_id: str = Field(..., description="Unique identifier for the document")
    score: float = Field(..., description="Retrieval score")
    text: str = Field(..., description="The text of the document")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _load_legacy_json_index(index_dir: str) -> list[dict]:
    index_path = Path(index_dir) / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"No index found at '{index_dir}'. Build it first with 'deepfix index "
            f"--dataset <dataset> --config <config> --index-dir {index_dir}'."
        )
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_lancedb_table(index_dir: str):
    out_dir = Path(index_dir)
    if not out_dir.exists():
        return None
    try:
        db = lancedb.connect(str(out_dir))
        tables = list(db.table_names()) if hasattr(db, "table_names") else list(db.list_tables())
        if not tables:
            return None
        table_name = "documents" if "documents" in tables else ("legal_passages" if "legal_passages" in tables else tables[0])
        return db.open_table(table_name)
    except Exception:
        return None


def execute_retrieval(
    query: str, config: RAGConfig, index_dir: str | None = None
) -> list[RetrievalResult]:
    """Execute retrieval over a LanceDB index (or fallback JSON index) for a query."""
    if not index_dir:
        raise ValueError("index_dir is required for retrieval.")

    embedder = EmbeddingClient(model=config.embedding.model, provider=config.embedding.provider)
    query_vector = embedder.embed_query(query)
    top_k = config.retrieval.top_k

    table = _get_lancedb_table(index_dir)
    if table is not None:
        try:
            if config.retrieval.backend == "hybrid":
                try:
                    res_df = (
                        table.search(query_type="hybrid")
                        .vector(query_vector)
                        .text(query)
                        .limit(top_k)
                        .to_pandas()
                    )
                except Exception:
                    res_df = table.search(query_vector).limit(top_k).to_pandas()
            else:
                res_df = table.search(query_vector).limit(top_k).to_pandas()

            results = []
            for _, row in res_df.iterrows():
                doc_id = str(row.get("id") or row.get("doc_id") or row.get("node_id") or "")
                score = float(row.get("_distance", row.get("score", 1.0)))
                text = str(row.get("text", ""))
                results.append(RetrievalResult(doc_id=doc_id, score=score, text=text))
            return results
        except Exception:
            pass

    entries = _load_legacy_json_index(index_dir)
    if not entries:
        return []

    scored = [
        (
            _cosine_similarity(query_vector, entry["embedding"]),
            entry,
        )
        for entry in entries
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        RetrievalResult(doc_id=str(entry.get("doc_id") or entry.get("id")), score=score, text=entry["text"])
        for score, entry in scored[:top_k]
    ]

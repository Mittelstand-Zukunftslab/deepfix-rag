"""RAG retrieval execution wrapper."""

import json
import math
from pathlib import Path

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


def _load_index(index_dir: str) -> list[dict]:
    index_path = Path(index_dir) / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"No index found at '{index_path}'. Build it first with 'deepfix index "
            f"--dataset <dataset> --config <config> --index-dir {index_dir}'."
        )
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def execute_retrieval(
    query: str, config: RAGConfig, index_dir: str | None = None
) -> list[RetrievalResult]:
    """Execute dense retrieval over a pre-built index for a query."""
    if not index_dir:
        raise ValueError("index_dir is required for retrieval.")

    entries = _load_index(index_dir)
    if not entries:
        return []

    embedder = EmbeddingClient(model=config.embedding.model, provider=config.embedding.provider)
    query_vector = embedder.embed_query(query)

    scored = [
        (
            _cosine_similarity(query_vector, entry["embedding"]),
            entry,
        )
        for entry in entries
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top_k = config.retrieval.top_k
    return [
        RetrievalResult(doc_id=entry["doc_id"], score=score, text=entry["text"])
        for score, entry in scored[:top_k]
    ]
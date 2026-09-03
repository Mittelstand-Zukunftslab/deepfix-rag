"""RAG retrieval execution wrapper using LanceDB (local & remote S3/RustFS) with fallback."""

import json
import math
import os
from pathlib import Path
from dotenv import load_dotenv

import lancedb
from pydantic import BaseModel, Field

from deepfix_rag.config.schema import UNIMPLEMENTED_BACKENDS, RAGConfig
from deepfix_rag.retrieval.models import EmbeddingClient, RerankClient

load_dotenv()


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


def _extract_table_names(db) -> list[str]:
    """Safely extract list of table names from LanceDB connection."""
    try:
        res = db.list_tables()
        if hasattr(res, "tables"):
            return list(res.tables)
        if isinstance(res, list):
            return res
    except Exception:
        pass
    try:
        return list(db.table_names())
    except Exception:
        return []


def _get_lancedb_table(index_dir: str):
    if index_dir.startswith("s3://"):
        try:
            storage_options = {
                "endpoint": os.getenv("RUSTFS_ENDPOINT", "https://hpi-s3.mittelstand-zukunftslab.de"),
                "aws_access_key_id": os.getenv("RUSTFS_ACCESS_KEY", "rustfsadmin"),
                "aws_secret_access_key": os.getenv("RUSTFS_SECRET_KEY", ""),
                "aws_region": "us-east-1",
                "allow_http": "true",
                "aws_virtual_hosted_style_request": "false",
            }
            db = lancedb.connect(index_dir, storage_options=storage_options)
            tabs = _extract_table_names(db)
            if not tabs:
                return None
            table_name = "legal_passages" if "legal_passages" in tabs else ("documents" if "documents" in tabs else tabs[0])
            return db.open_table(table_name)
        except Exception:
            return None

    out_dir = Path(index_dir)
    if not out_dir.exists():
        return None
    try:
        db = lancedb.connect(str(out_dir))
        tabs = _extract_table_names(db)
        if not tabs:
            return None
        table_name = "documents" if "documents" in tabs else ("legal_passages" if "legal_passages" in tabs else tabs[0])
        return db.open_table(table_name)
    except Exception:
        return None


def execute_retrieval(
    query: str, config: RAGConfig, index_dir: str | None = None
) -> list[RetrievalResult]:
    """Execute retrieval over a LanceDB index (or fallback JSON index) for a query."""
    if not index_dir:
        raise ValueError("index_dir is required for retrieval.")

    if config.retrieval.backend in UNIMPLEMENTED_BACKENDS:
        raise NotImplementedError(
            f"Retrieval backend '{config.retrieval.backend}' is declared in the schema but not "
            f"yet implemented in execute_retrieval - see issue #2."
        )

    embedder = EmbeddingClient(model=config.embedding.model, provider=config.embedding.provider)
    query_vector = embedder.embed_query(query)
    
    # top_k is our initial broad net size
    top_k = config.retrieval.top_k

    results = []
    
    # 1. Broad Retrieval (LanceDB or JSON)
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

            for _, row in res_df.iterrows():
                doc_id = str(row.get("id") or row.get("doc_id") or row.get("node_id") or "")
                score = float(row.get("_distance", row.get("score", 1.0)))
                text = str(row.get("text", ""))
                results.append(RetrievalResult(doc_id=doc_id, score=score, text=text))
        except Exception:
            pass
            
    if not results:
        entries = _load_legacy_json_index(index_dir)
        if entries:
            scored = [
                (
                    _cosine_similarity(query_vector, entry["embedding"]),
                    entry,
                )
                for entry in entries
            ]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            results = [
                RetrievalResult(doc_id=str(e.get("doc_id") or e.get("id")), score=s, text=e["text"])
                for s, e in scored[:top_k]
            ]
            
    # 2. Advanced Cross-Encoder Re-Ranking Stage
    if results and config.rerank.enabled and getattr(config.rerank, "model", None):
        reranker = RerankClient(model=config.rerank.model)
        doc_texts = [r.text for r in results]
        
        # Ask Qwen Reranker to score our texts and give back the final top indices
        top_indices = reranker.rerank(query, doc_texts, top_n=config.rerank.top_n)
        
        re_ranked = []
        # Assign a synthesized score based on their new rank order (0=highest, N=lowest rank) 
        # because the API returns indices grouped by highest relevance first.
        for rank, old_index in enumerate(top_indices):
            best_match = results[old_index]
            # Replace the dense/vector score with its new cross-decoder rank
            best_match.score = float(len(top_indices) - rank)  
            re_ranked.append(best_match)
            
        results = re_ranked
        
    return results

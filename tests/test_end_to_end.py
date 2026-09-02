"""Integration and end-to-end testing for DeepFix RAG pipeline with Octen & LanceDB."""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import lancedb

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.retrieval.models import EmbeddingClient, GenerationClient
from deepfix_rag.retrieval.pipeline import execute_retrieval

load_dotenv()


def test_e2e_pipeline_flow():
    # 1. Embedding with Octen-8B
    embedder = EmbeddingClient(model="octen-embedding-8b", provider="aisc")
    query = "What constitutes reckless driving under traffic law?"
    query_vec = embedder.embed_query(query)
    assert len(query_vec) == 4096

    docs = [
        "Reckless driving is operating a vehicle with willful or wanton disregard for safety.",
        "Speeding alone does not always constitute reckless driving without dangerous maneuvers.",
        "A tort is a civil wrong causing harm.",
    ]
    doc_vecs = embedder.embed_documents(docs)
    assert len(doc_vecs) == 3
    assert len(doc_vecs[0]) == 4096

    # 2. Local LanceDB indexing
    test_dir = Path("data/test_pytest_lancedb")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    db = lancedb.connect(str(test_dir))
    df = pd.DataFrame([
        {"id": f"doc_{i}", "doc_id": f"doc_{i}", "text": text, "vector": vec}
        for i, (text, vec) in enumerate(zip(docs, doc_vecs))
    ])
    tbl = db.create_table("documents", data=df, mode="overwrite")
    assert tbl.count_rows() == 3

    # 3. Retrieval
    cfg = RAGConfig.model_validate({
        "embedding": {"model": "octen-embedding-8b", "provider": "aisc"},
        "retrieval": {"backend": "hybrid", "top_k": 2},
    })
    results = execute_retrieval(query, cfg, index_dir=str(test_dir))
    assert len(results) == 2
    assert any("reckless" in r.text.lower() for r in results)

    # 4. Generation
    generator = GenerationClient(model="openai/openrouter/qwen/qwen3.8-27b", provider="hpi")
    answer, usage = generator.generate(query, [r.text for r in results], return_usage=True)
    assert len(answer) > 0
    assert "rag.total_tokens" in usage

    # Cleanup
    if test_dir.exists():
        shutil.rmtree(test_dir)

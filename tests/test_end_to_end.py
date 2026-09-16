import shutil
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import lancedb

from llama_index.core.base.embeddings.base import BaseEmbedding
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.pipeline import RAGWorkflow, execute_retrieval
from deepfix_rag.data import DataLoader


class MockEmbedding(BaseEmbedding):
    def _get_text_embedding(self, text: str) -> list[float]:
        return [0.1] * 128

    def _get_query_embedding(self, query: str) -> list[float]:
        return [0.1] * 128

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return [0.1] * 128

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return [0.1] * 128


def test_e2e_pipeline_flow():
    test_dir = Path("data/test_pytest_lancedb")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    docs = [
        "Reckless driving is operating a vehicle with willful or wanton disregard for safety.",
        "Speeding alone does not always constitute reckless driving without dangerous maneuvers.",
        "A tort is a civil wrong causing harm.",
    ]
    data_file = test_dir / "corpus.jsonl"
    with open(data_file, "w", encoding="utf-8") as f:
        for i, text in enumerate(docs):
            f.write(f'{{"doc_id": "doc_{i}", "text": "{text}"}}\n')

    cfg = RAGConfig()
    cfg.rerank.enabled = False

    # 1. Test DataLoader
    loader = DataLoader()
    loaded_docs = loader.load_documents(data_file=str(data_file))
    assert len(loaded_docs) == 3

    # 2. Test Ingestion via RAGWorkflow using MockEmbedding
    mock_embed = MockEmbedding()
    workflow = RAGWorkflow(config=cfg, index_dir=str(test_dir), embed_model=mock_embed)
    index = workflow._build_index(loaded_docs)
    assert index is not None

    # Check LanceDB table
    db = lancedb.connect(str(test_dir))
    tbl = db.open_table("documents")
    assert tbl.count_rows() == 3

    # 3. Test Retrieval
    query = "What constitutes reckless driving under traffic law?"
    results = execute_retrieval(query, cfg, index_dir=str(test_dir), workflow=workflow)
    assert len(results) > 0
    assert any("reckless" in r.text.lower() for r in results)

from pathlib import Path

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.data import DataLoaderConfig, DataSourceType
from deepfix_rag.pipeline import RAGWorkflow, execute_pipeline


def test_e2e_pipeline_flow(tmp_path: Path):
    """Verify full end-to-end ingestion, indexing, and retrieval using authentic benchmark data and live services."""
    assert settings.EMBEDDING_API_KEY, (
        "EMBEDDING_API_KEY must be configured in .env to run real end-to-end embedding tests without mocks."
    )

    sample_corpus_file = Path(__file__).parent / "data" / "sample_corpus.jsonl"
    assert sample_corpus_file.exists(), f"Sample benchmark corpus not found at {sample_corpus_file}"

    index_dir = tmp_path / "test_lancedb_index"

    cfg = RAGConfig()
    cfg.retrieval.language = "german"
    cfg.retrieval.top_k = 3
    cfg.rerank.enabled = False
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.data = DataLoaderConfig(
        source_type=DataSourceType.LOCAL_FILE,
        path_or_repo=str(sample_corpus_file),
        doc_id_column="doc_id",
        text_column="text",
    )

    workflow = RAGWorkflow(config=cfg)    

    # 3. Test Retrieval with real hybrid search (dense embedding + BM25 with German stemmer)
    query = "Was regelt § 315c StGB bezüglich Trunkenheit im Straßenverkehr?"
    results = execute_pipeline(query,workflow=workflow,ingest_data=True)

    print(results)

    assert len(results) > 0
    top_result = results[0]
    assert top_result.score is not None
    # Relevant document about § 315c StGB must be retrieved
    assert any("315c" in r.text for r in results)
    assert any("alkohol" in r.text.lower() or "trunkenheit" in r.text.lower() or "straßenverkehr" in r.text.lower() for r in results)

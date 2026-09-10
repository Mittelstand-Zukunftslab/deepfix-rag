import shutil
from pathlib import Path

import lancedb
import pandas as pd


from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.retrieval.models import GenerationClient, AISCEmbedding
from deepfix_rag.evaluation.indexing import load_corpus, build_index
from deepfix_rag.retrieval.pipeline import execute_retrieval




def test_e2e_pipeline_flow():
    # 1. Embedding with Octen-8B
    embedder = AISCEmbedding(model="octen-embedding-8b", provider="aisc")
    query = "What constitutes reckless driving under traffic law?"
    query_vec = embedder._get_query_embedding(query)
    assert len(query_vec) == 4096

    docs = [
        "Reckless driving is operating a vehicle with willful or wanton disregard for safety.",
        "Speeding alone does not always constitute reckless driving without dangerous maneuvers.",
        "A tort is a civil wrong causing harm.",
    ]
    doc_vecs = embedder._get_text_embeddings(docs)
    assert len(doc_vecs) == 3
    assert len(doc_vecs[0]) == 4096

    # 2. Local LlamaIndex LanceDB indexing
    test_dir = Path("data/test_pytest_lancedb")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # We create a dummy jsonl file which indexer consumes
    data_file = test_dir / "corpus.jsonl"
    with open(data_file, "w") as f:
        for i, text in enumerate(docs):
            f.write(f'{{"doc_id": "doc_{i}", "text": "{text}"}}\n')
            
    cfg = RAGConfig.model_validate({
        "embedding": {"model": "octen-embedding-8b", "provider": "aisc"},
        "retrieval": {"backend": "hybrid", "top_k": 2},
        "chunking": {"size": 200, "overlap": 20},
    })
    
    # Build with correct LlamaIndex native mapping
    build_index(data_file, cfg, str(test_dir))
    
    # Check rows cleanly 
    db = lancedb.connect(str(test_dir))
    tbl = db.open_table("documents")
    assert tbl.count_rows() == 3

    # 3. Retrieval
    results = execute_retrieval(query, cfg, index_dir=str(test_dir))
    assert len(results) > 0
    assert any("reckless" in r.text.lower() for r in results)

    # 4. Generation
    generator = GenerationClient(model="qwen3-8-27b", provider="aisc")
    answer, usage = generator.generate(query, [r.text for r in results], return_usage=True)
    assert len(answer) > 0
    assert "prompt_tokens" in usage

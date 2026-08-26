from deepfix_rag.config.mutator import apply_mutation, compute_config_diff
from deepfix_rag.config.schema import RAGConfig


def test_rag_config_defaults():
    config = RAGConfig()
    assert config.chunking.size == 512
    assert config.embedding.model == "octen-embedding-8b"
    assert config.embedding.provider == "aisc"
    assert config.retrieval.top_k == 10


def test_apply_mutation():
    base = RAGConfig()
    mutated = apply_mutation(base, {"chunking.size": 1024, "retrieval.top_k": 20})
    assert mutated.chunking.size == 1024
    assert mutated.retrieval.top_k == 20
    assert base.chunking.size == 512  # original unchanged


def test_compute_config_diff():
    base = RAGConfig()
    mutated = apply_mutation(base, {"chunking.size": 1024})
    diffs = compute_config_diff(base, mutated)
    assert "chunking.size" in diffs
    assert diffs["chunking.size"] == (512, 1024)

import pytest
from pydantic import ValidationError

from deepfix_rag.config.mutator import apply_mutation, compute_config_diff
from deepfix_rag.config.schema import RAGConfig, RetrievalBackend


def test_rag_config_defaults():
    config = RAGConfig()
    assert config.chunking.size == 512
    assert config.retrieval.top_k == 10
    assert config.retrieval.backend == RetrievalBackend.HYBRID


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


def test_retrieval_backend_rejects_invalid_value():
    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"backend": "not-a-real-backend"}})


def test_retrieval_backend_accepts_declared_values():
    for backend in (RetrievalBackend.BM25, RetrievalBackend.DENSE, RetrievalBackend.HYBRID, RetrievalBackend.GRAPH):
        config = RAGConfig.model_validate({"retrieval": {"backend": backend}})
        assert config.retrieval.backend == backend

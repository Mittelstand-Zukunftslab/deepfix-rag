"""Tests for RAGConfig schema defaults, mutation operations, and validation rules."""

from pydantic import ValidationError
import pytest

from deepfix_rag.config.mutator import apply_mutation, compute_config_diff
from deepfix_rag.config.schema import RAGConfig, RetrievalBackend

pytestmark = [pytest.mark.unit]


def test_rag_config_defaults(default_rag_config: RAGConfig):
    """Verify default parameters of RAGConfig."""
    assert default_rag_config.chunking.size == 512
    assert default_rag_config.retrieval.top_k == 10
    assert default_rag_config.retrieval.backend == RetrievalBackend.HYBRID
    assert default_rag_config.retrieval.language == "german"
    assert default_rag_config.retrieval.bit_width == 4


def test_apply_mutation(default_rag_config: RAGConfig):
    """Verify immutable mutation logic correctly updates target fields without altering original."""
    mutated = apply_mutation(
        default_rag_config,
        {
            "chunking.size": 1024,
            "retrieval.top_k": 20,
            "retrieval.language": "english",
            "retrieval.bit_width": 2,
        },
    )
    assert mutated.chunking.size == 1024
    assert mutated.retrieval.top_k == 20
    assert mutated.retrieval.language == "english"
    assert mutated.retrieval.bit_width == 2
    assert default_rag_config.chunking.size == 512  # original unchanged
    assert default_rag_config.retrieval.language == "german"


def test_compute_config_diff(default_rag_config: RAGConfig):
    """Verify delta computation between base and mutated configurations."""
    mutated = apply_mutation(
        default_rag_config,
        {"chunking.size": 1024, "retrieval.language": "english"},
    )
    diffs = compute_config_diff(default_rag_config, mutated)
    assert "chunking.size" in diffs
    assert diffs["chunking.size"] == (512, 1024)
    assert "retrieval.language" in diffs
    assert diffs["retrieval.language"] == ("german", "english")


def test_retrieval_backend_rejects_invalid_value():
    """Verify Pydantic validation error when an unknown backend is supplied."""
    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"backend": "not-a-real-backend"}})


@pytest.mark.parametrize(
    "backend",
    [
        RetrievalBackend.BM25,
        RetrievalBackend.DENSE,
        RetrievalBackend.HYBRID,
        RetrievalBackend.GRAPH,
    ],
)
def test_retrieval_backend_accepts_declared_values(backend: RetrievalBackend):
    """Verify all valid RetrievalBackend enum variants parse correctly."""
    config = RAGConfig.model_validate({"retrieval": {"backend": backend}})
    assert config.retrieval.backend == backend


def test_retrieval_bit_width_validation():
    """Verify quantization bit-width restrictions."""
    # Valid bit widths are 2, 3, 4
    cfg = RAGConfig.model_validate({"retrieval": {"bit_width": 2}})
    assert cfg.retrieval.bit_width == 2

    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"bit_width": 1}})

    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"bit_width": 5}})


@pytest.mark.parametrize(
    "config_filename",
    ["default.yaml", "dense.yaml", "graph.yaml", "rerank.yaml"],
)
def test_predefined_config_files_valid(config_filename: str):
    """Verify all repository configuration templates match RAGConfig schema."""
    from pathlib import Path

    from deepfix_rag.cli.utils import load_rag_config

    config_path = Path("configs") / config_filename
    assert config_path.exists()
    config = load_rag_config(config_path)
    assert isinstance(config, RAGConfig)
    assert config.retrieval.load_if_exists is True

"""Tests for RAGConfig schema defaults, mutation operations, and validation rules."""

from pydantic import ValidationError
import pytest

from deepfix_rag.config.mutator import apply_mutation, compute_config_diff
from deepfix_rag.config.schema import RAGConfig, RetrievalBackend

pytestmark = [pytest.mark.unit]


def test_rag_config_defaults(default_rag_config: RAGConfig):
    """Verify default parameters of RAGConfig."""
    assert default_rag_config.chunking.size == 1024
    assert default_rag_config.retrieval.top_k == 10
    assert default_rag_config.retrieval.backend == RetrievalBackend.HYBRID
    assert default_rag_config.retrieval.language == "german"
    assert default_rag_config.retrieval.bit_width == 4
    # The floor is calibrated for the served reranker, not an arbitrary 0.2.
    assert default_rag_config.rerank.min_relevance_score == 0.3


def test_apply_mutation(default_rag_config: RAGConfig):
    """Verify immutable mutation logic correctly updates target fields without altering original."""
    mutated = apply_mutation(
        default_rag_config,
        {
            "chunking.size": 2048,
            "retrieval.top_k": 20,
            "retrieval.language": "english",
            "retrieval.bit_width": 2,
        },
    )
    assert mutated.chunking.size == 2048
    assert mutated.retrieval.top_k == 20
    assert mutated.retrieval.language == "english"
    assert mutated.retrieval.bit_width == 2
    assert default_rag_config.chunking.size == 1024  # original unchanged
    assert default_rag_config.retrieval.language == "german"


def test_compute_config_diff(default_rag_config: RAGConfig):
    """Verify delta computation between base and mutated configurations."""
    mutated = apply_mutation(
        default_rag_config,
        {"chunking.size": 2048, "retrieval.language": "english"},
    )
    diffs = compute_config_diff(default_rag_config, mutated)
    assert "chunking.size" in diffs
    assert diffs["chunking.size"] == (1024, 2048)
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
        RetrievalBackend.MIXED,
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
    ["default.yaml", "dense.yaml", "graph.yaml", "graph_falkordb.yml", "rerank.yaml"],
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


def test_mixed_backend_configuration():
    """Verify MIXED backend configuration with custom component weights."""
    cfg = RAGConfig.model_validate(
        {
            "retrieval": {
                "backend": RetrievalBackend.MIXED,
                "dense_weight": 0.4,
                "bm25_weight": 0.3,
                "graph_weight": 0.3,
            }
        }
    )
    assert cfg.retrieval.backend == RetrievalBackend.MIXED
    assert cfg.retrieval.dense_weight == 0.4
    assert cfg.retrieval.bm25_weight == 0.3
    assert cfg.retrieval.graph_weight == 0.3


def test_graph_extraction_schemas_in_schema_module():
    """Verify GRAPH_EXTRACTION_SCHEMAS is defined as GraphExtractionSchema BaseModel instances."""
    from deepfix_rag.config.schema import GRAPH_EXTRACTION_SCHEMAS, GraphExtractionSchema

    assert "entity_relationship" in GRAPH_EXTRACTION_SCHEMAS
    assert "domain_legal" in GRAPH_EXTRACTION_SCHEMAS
    assert isinstance(GRAPH_EXTRACTION_SCHEMAS["entity_relationship"], GraphExtractionSchema)
    assert isinstance(GRAPH_EXTRACTION_SCHEMAS["domain_legal"], GraphExtractionSchema)
    assert len(GRAPH_EXTRACTION_SCHEMAS["entity_relationship"].entities) > 0
    assert len(GRAPH_EXTRACTION_SCHEMAS["entity_relationship"].relations) > 0
    assert len(GRAPH_EXTRACTION_SCHEMAS["domain_legal"].entities) > 0
    assert len(GRAPH_EXTRACTION_SCHEMAS["domain_legal"].relations) > 0


def test_bundestag_schema_is_the_closed_dip_ontology():
    """The Bundestag entry declares the DIP vocabulary and only the allowed type pairs."""
    from deepfix_rag.config.schema import BUNDESTAG_TRIPLES, GRAPH_EXTRACTION_SCHEMAS

    assert set(GRAPH_EXTRACTION_SCHEMAS) == {
        "entity_relationship",
        "domain_legal",
        "bundestag",
    }
    bundestag = GRAPH_EXTRACTION_SCHEMAS["bundestag"]
    assert set(bundestag.entities) == {
        "PERSON",
        "FRAKTION",
        "MINISTERIUM",
        "AUSSCHUSS",
        "DRUCKSACHE",
        "PLENARPROTOKOLL",
        "VORGANG",
        "GESETZ",
        "THEMA",
    }
    assert set(bundestag.relations) == {
        "MEMBER_OF",
        "SUBMITTED_BY",
        "ANSWERED_BY",
        "ASSIGNED_TO",
        "PART_OF",
        "AMENDS",
        "REFERS_TO",
        "SPOKE_IN",
        "ABOUT",
    }
    # The pair list is closed: 23 triples, no duplicates, and every part declared.
    assert len(bundestag.validation_triples) == 23
    assert len(set(bundestag.validation_triples)) == 23
    assert set(bundestag.validation_triples) == set(BUNDESTAG_TRIPLES)
    for subject, relation, obj in bundestag.validation_triples:
        assert subject in bundestag.entities
        assert obj in bundestag.entities
        assert relation in bundestag.relations
    # A pair the corpus cannot mean is absent, which is what strict mode enforces.
    assert ("AUSSCHUSS", "SUBMITTED_BY", "THEMA") not in set(bundestag.validation_triples)


def test_the_fallback_schemas_close_their_own_vocabulary():
    """Strict mode must not start rejecting pairs the generic schemas already allowed."""
    from deepfix_rag.config.schema import GRAPH_EXTRACTION_SCHEMAS

    for name in ("entity_relationship", "domain_legal"):
        schema = GRAPH_EXTRACTION_SCHEMAS[name]
        expected = {
            (subject, relation, obj)
            for relation in schema.relations
            for subject in schema.entities
            for obj in schema.entities
        }
        assert set(schema.validation_triples) == expected

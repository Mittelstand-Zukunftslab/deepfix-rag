"""Tests for CLI index command, directory validation, overwrite protection, and index persistence."""

import asyncio
import json
from pathlib import Path

from llama_index.core.retrievers.fusion_retriever import QueryFusionRetriever
import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.cli.utils import load_rag_config
from deepfix_rag.config.schema import FusionMode, GraphStoreType, RAGConfig, RetrievalBackend
from deepfix_rag.config.settings import settings
from deepfix_rag.main import app
from deepfix_rag.pipeline import RAGWorkflow

pytestmark = [pytest.mark.cli, pytest.mark.integration]


def _assert_index_metadata_payload(
    payload: dict, *, paradigm: str, require_storage: bool = True
) -> None:
    """Assert the shared US-004 index build metadata contract."""
    assert payload["status"] == "success"
    assert payload["paradigm"] == paradigm
    assert payload["document_count"] == 5
    assert payload["chunk_statistics"]["count"] >= 1
    assert payload["chunk_statistics"]["strategy"] == "recursive"
    assert payload["chunk_statistics"]["size"] == 512
    assert payload["chunk_statistics"]["overlap"] == 64
    assert payload["build_duration_seconds"] >= 0.0
    assert payload["storage_bytes"] >= 0
    if require_storage:
        assert payload["storage_bytes"] > 0
    assert payload["embedding_model"] == settings.EMBEDDING_MODEL
    assert payload["reranker_model"] == settings.COHERE_MODEL


def test_cli_index_cmd_missing_force_fails(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    default_config_path: Path,
    tmp_path: Path,
):
    """Verify that indexing into an existing non-empty directory requires --force."""
    index_dir = tmp_path / "existing_index"
    index_dir.mkdir()
    (index_dir / "marker.txt").write_text("existing data", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "index",
            "--dataset",
            str(sample_corpus_path),
            "--config",
            str(default_config_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "already exists" in payload["error"]
    assert "--force" in payload["error"]


def test_cli_index_cmd_with_force_succeeds(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    default_config_path: Path,
    tmp_path: Path,
):
    """Verify that --force overwrites existing index directory and creates LanceDB index on disk."""
    index_dir = tmp_path / "existing_index"
    index_dir.mkdir()
    (index_dir / "marker.txt").write_text("existing data", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "index",
            "--dataset",
            str(sample_corpus_path),
            "--config",
            str(default_config_path),
            "--index-dir",
            str(index_dir),
            "--force",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "index_path" in payload
    _assert_index_metadata_payload(payload, paradigm="hybrid")

    # Verify that marker.txt was deleted and LanceDB documents dataset was created
    assert not (index_dir / "marker.txt").exists()
    assert (index_dir / "documents.lance").exists()


def test_cli_index_cmd_declarative_config_only(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify that deepfix index succeeds declaratively using only --config."""
    index_dir = tmp_path / "declarative_index"
    config_path = tmp_path / "declarative_config.yaml"

    cfg = RAGConfig()
    cfg.data.path_or_repo = str(sample_corpus_path)
    cfg.retrieval.lancedb_index_dir = str(index_dir)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.model_dump(mode="json"), f)

    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            str(config_path),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["index_path"] == str(index_dir)
    assert (index_dir / "documents.lance").exists()
    _assert_index_metadata_payload(payload, paradigm="hybrid")


def test_cli_index_cmd_declarative_with_cli_overrides(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify that CLI flags override values declared in the config YAML."""
    config_index_dir = tmp_path / "config_index"
    cli_index_dir = tmp_path / "cli_index"
    config_path = tmp_path / "override_config.yaml"

    cfg = RAGConfig()
    cfg.data.path_or_repo = str(sample_corpus_path)
    cfg.retrieval.lancedb_index_dir = str(config_index_dir)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.model_dump(mode="json"), f)

    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            str(config_path),
            "--index-dir",
            str(cli_index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["index_path"] == str(cli_index_dir)
    assert (cli_index_dir / "documents.lance").exists()
    assert not config_index_dir.exists()
    _assert_index_metadata_payload(payload, paradigm="hybrid")


def test_cli_index_dense_config_json_metadata(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify configs/dense.yaml builds a LanceDB index and returns US-004 metadata."""
    index_dir = tmp_path / "dense_index"
    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            "configs/dense.yaml",
            "--dataset",
            str(sample_corpus_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.stdout
    payload = json.loads(result.stdout)
    assert payload["index_path"] == str(index_dir)
    assert (index_dir / "documents.lance").exists()
    _assert_index_metadata_payload(payload, paradigm="dense")
    assert payload["embedding_model"] == "octen-embedding-8b"


def test_cli_index_rerank_config_exposes_reranker_model(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify configs/rerank.yaml reports qwen3-reranker-4b and constructs CohereRerank."""
    index_dir = tmp_path / "rerank_index"
    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            "configs/rerank.yaml",
            "--dataset",
            str(sample_corpus_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.stdout
    payload = json.loads(result.stdout)
    assert payload["reranker_model"] == "qwen3-reranker-4b"
    assert payload["paradigm"] == "hybrid"
    assert (index_dir / "documents.lance").exists()

    cfg = load_rag_config(Path("configs/rerank.yaml"))
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.retrieval.load_if_exists = True
    workflow = RAGWorkflow(config=cfg)
    assert cfg.rerank.enabled is True
    reranker = workflow._get_reranker()
    assert reranker is not None
    assert reranker.model == "qwen3-reranker-4b"


def test_cli_index_hybrid_default_config_retrieve_and_rrf(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify configs/default.yaml hybrid indexing supports retrieve and configurable RRF."""
    index_dir = tmp_path / "hybrid_index"
    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            "configs/default.yaml",
            "--dataset",
            str(sample_corpus_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.stdout
    payload = json.loads(result.stdout)
    _assert_index_metadata_payload(payload, paradigm="hybrid")
    assert (index_dir / "documents.lance").exists()

    cfg = load_rag_config(Path("configs/default.yaml"))
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.retrieval_only = True
    assert cfg.retrieval.backend == RetrievalBackend.HYBRID
    assert cfg.retrieval.fusion_mode == FusionMode.RECIPROCAL_RANK

    workflow = RAGWorkflow(config=cfg)

    async def _ingest_and_query():
        index = await workflow.run(ingest=True)
        retriever = workflow._build_retriever(
            index=index,
            top_k=cfg.retrieval.top_k,
            language=cfg.retrieval.language,
            use_async=True,
        )
        assert isinstance(retriever, QueryFusionRetriever)
        mode_value = getattr(retriever.mode, "value", retriever.mode)
        assert mode_value == "reciprocal_rerank"
        return await workflow.run(
            query="Wann liegt eine Gefährdung des Straßenverkehrs nach § 315c StGB vor?",
            index=index,
        )

    nodes = asyncio.run(_ingest_and_query())
    assert nodes
    assert len(nodes) > 0


def test_cli_index_graph_config_json_and_retrieve(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
):
    """Verify configs/graph_falkordb.yml builds PropertyGraphIndex on FalkorDB and supports retrieve."""
    assert settings.FALKORDB_HOST
    assert settings.FALKORDB_PORT > 0

    index_dir = tmp_path / "graph_index"
    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            "configs/graph_falkordb.yml",
            "--dataset",
            str(sample_corpus_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.stdout
    payload = json.loads(result.stdout)
    # Graph stores entities in FalkorDB; local LanceDB footprint may be empty.
    _assert_index_metadata_payload(payload, paradigm="graph", require_storage=False)
    assert payload["paradigm"] == "graph"

    cfg = load_rag_config(Path("configs/graph_falkordb.yml"))
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.retrieval_only = True
    assert cfg.retrieval.backend == RetrievalBackend.GRAPH
    assert cfg.retrieval.graph_store.value == "falkordb"

    workflow = RAGWorkflow(config=cfg)
    graph_store = workflow._get_graph_store()
    assert graph_store is not None
    assert type(graph_store).__name__ == "FalkorDBPropertyGraphStore"

    async def _query_existing():
        index = workflow._load_existing_index()
        workflow._index = index
        return await workflow.run(
            query="Wann liegt eine Gefährdung des Straßenverkehrs nach § 315c StGB vor?",
            index=index,
        )

    nodes = asyncio.run(_query_existing())
    assert nodes is not None


def test_graph_index_exists_when_lancedb_table_has_rows(tmp_path: Path) -> None:
    """A FalkorDB config reuses a LanceDB table instead of extracting again."""
    import lancedb

    index_dir = tmp_path / "graph_index"
    connection = lancedb.connect(str(index_dir))
    connection.create_table("documents", data=[{"id": "1", "text": "bundestag"}])

    config = RAGConfig()
    config.retrieval.backend = RetrievalBackend.GRAPH
    config.retrieval.graph_store = GraphStoreType.FALKORDB
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = True

    workflow = RAGWorkflow(config=config)
    assert workflow._index_exists() is True


def test_graph_index_missing_when_lance_table_is_absent(tmp_path: Path) -> None:
    """An empty directory is not an existing graph index."""
    index_dir = tmp_path / "empty_graph"
    index_dir.mkdir()
    config = RAGConfig()
    config.retrieval.backend = RetrievalBackend.GRAPH
    config.retrieval.graph_store = GraphStoreType.FALKORDB
    config.retrieval.lancedb_index_dir = str(index_dir)

    workflow = RAGWorkflow(config=config)
    assert workflow._index_exists() is False


def test_entity_relationship_schema_builds_extractor() -> None:
    """Entity lists must be Literal types, which is what the extractor validates."""
    from llama_index.core.indices.property_graph import SchemaLLMPathExtractor

    from deepfix_rag.config.schema import GRAPH_EXTRACTION_SCHEMAS
    from deepfix_rag.pipeline import label_literal, validation_triples

    schema = GRAPH_EXTRACTION_SCHEMAS["entity_relationship"]
    extractor = SchemaLLMPathExtractor(
        llm=RAGWorkflow(config=RAGConfig())._get_llm(),
        possible_entities=label_literal(schema.entities),
        possible_relations=label_literal(schema.relations),
        kg_validation_schema=validation_triples(schema.entities, schema.relations),
        max_triplets_per_chunk=5,
    )
    assert extractor.max_triplets_per_chunk == 5
    assert ("PERSON", "RELATED_TO", "ORGANIZATION") in extractor.kg_validation_schema[
        "relationships"
    ]

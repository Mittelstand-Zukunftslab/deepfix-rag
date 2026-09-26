"""Tests for dataset loader routing, configuration inference, and local parsing."""

from pathlib import Path

from llama_index.core.schema import Document, NodeWithScore, TextNode
import pytest

from deepfix_rag.config.schema import (
    ChunkingConfig,
    ChunkingStrategy,
    DataLoaderConfig,
    DataPreset,
    DataSourceType,
    RAGConfig,
    RetrievalBackend,
)
from deepfix_rag.data import (
    BundestagLoader,
    DataLoader,
    HuggingFaceLoader,
    LegalRagBenchLoader,
    QARecord,
    build_node_parser,
)
from deepfix_rag.pipeline import RAGWorkflow

pytestmark = [pytest.mark.unit]


def test_dataloader_config_inference_and_defaults(tmp_path: Path):
    """Verify inference of DataSourceType and preset configurations."""
    # Default is BUNDESTAG_CORPUS and HUGGINGFACE
    cfg_default = DataLoaderConfig()
    assert cfg_default.path_or_repo == DataPreset.BUNDESTAG_CORPUS
    assert cfg_default.source_type == DataSourceType.HUGGINGFACE

    # Preset Bundestag QA
    cfg_qa = DataLoaderConfig(path_or_repo=DataPreset.BUNDESTAG_QA)
    assert cfg_qa.source_type == DataSourceType.HUGGINGFACE

    # Local file inference
    sample_file = tmp_path / "test.jsonl"
    sample_file.write_text('{"text": "hello", "id": "1"}\n', encoding="utf-8")
    cfg_file = DataLoaderConfig(path_or_repo=str(sample_file))
    assert cfg_file.source_type == DataSourceType.LOCAL_FILE

    # Local directory inference
    cfg_dir = DataLoaderConfig(path_or_repo=str(tmp_path))
    assert cfg_dir.source_type == DataSourceType.DIRECTORY


def test_bundestag_loader_canonical_repos():
    """Verify canonical HuggingFace repository identifiers for Bundestag loader."""
    loader = BundestagLoader()
    assert loader.CORPUS_REPO == "wirthual/dip-bundestag"
    assert loader.QA_REPO == "wirthual/dip-bundestag-qa"


def test_huggingface_loader_routing():
    """Verify dynamic loader resolution from preset configurations."""
    hf_loader = HuggingFaceLoader()

    # Routing to Bundestag
    b_loader = hf_loader._resolve_loader(
        DataLoaderConfig(path_or_repo=DataPreset.BUNDESTAG_CORPUS)
    )
    assert isinstance(b_loader, BundestagLoader)

    # Routing to Legal RAG Bench
    l_loader = hf_loader._resolve_loader(DataLoaderConfig(path_or_repo=DataPreset.LEGAL_RAG_BENCH))
    assert isinstance(l_loader, LegalRagBenchLoader)


def test_local_file_loader(tmp_path: Path):
    """Verify parsing of custom local corpus and QA files."""
    corpus_file = tmp_path / "corpus.jsonl"
    corpus_file.write_text(
        '{"id": "c1", "text": "Corpus document text."}\n',
        encoding="utf-8",
    )
    qa_file = tmp_path / "qa.jsonl"
    qa_file.write_text(
        '{"query_id": "q1", "query": "Frage?", "answer": "Antwort.", "doc_id": "c1"}\n',
        encoding="utf-8",
    )

    data_loader = DataLoader(DataLoaderConfig(path_or_repo=str(corpus_file)))
    docs = data_loader.load_corpus()
    assert len(docs) == 1
    assert docs[0].id_ == "c1"
    assert docs[0].text == "Corpus document text."

    qa = DataLoader(DataLoaderConfig(path_or_repo=str(qa_file))).load_qa()
    assert len(qa) == 1
    assert qa[0].query == "Frage?"
    assert qa[0].answer == "Antwort."


def test_sample_benchmark_fixtures_loading(sample_corpus_path: Path, sample_qa_path: Path):
    """Verify shared sample fixtures can be loaded via DataLoader."""
    data_loader = DataLoader(DataLoaderConfig(path_or_repo=str(sample_corpus_path)))
    docs = data_loader.load_corpus()
    assert len(docs) > 0
    assert docs[0].id_ is not None

    qa = DataLoader(DataLoaderConfig(path_or_repo=str(sample_qa_path))).load_qa()
    assert len(qa) > 0
    assert hasattr(qa[0], "query")


def test_to_qrels_df_single_doc_ids():
    """Verify to_qrels_df creates proper qrels dataframe from QARecord objects."""
    qa_records = [
        QARecord(query_id="q1", query="Q1?", answer="A1", doc_id="d1"),
        QARecord(query_id="q2", query="Q2?", answer="A2", doc_id="d2"),
        QARecord(query_id="q3", query="Q3?", answer="A3", doc_id="d3"),
    ]
    df = DataLoader.to_qrels_df(qa_records)
    assert list(df.columns) == ["qid", "docno", "label", "rank", "query"]
    assert len(df) == 3
    assert set(df["qid"]) == {"q1", "q2", "q3"}
    assert set(df["docno"]) == {"d1", "d2", "d3"}
    assert all(df["label"] == 1)


def test_to_qrels_df_empty():
    """Verify to_qrels_df returns typed empty DataFrame for empty inputs."""
    df = DataLoader.to_qrels_df([])
    assert list(df.columns) == ["qid", "docno", "label", "rank"]
    assert len(df) == 0


def test_load_qrels_from_sample_qa(sample_qa_path: Path):
    """Verify load_qrels produces valid PyTerrier qrels DataFrame from sample QA file."""
    data_loader = DataLoader(DataLoaderConfig(path_or_repo=str(sample_qa_path)))
    qrels = data_loader.load_qrels()
    assert len(qrels) == 3
    assert set(qrels["qid"]) == {"q_de_001", "q_de_002", "q_de_003"}
    assert set(qrels["docno"]) == {"doc_de_001", "doc_de_004", "doc_de_003"}
    assert all(qrels["label"] == 1)


def test_load_corpus_includes_gold_ids_when_limit_is_smaller(
    sample_corpus_path: Path, sample_qa_path: Path
) -> None:
    """Verify required gold documents are kept even when limit is below the gold set size."""
    qa = DataLoader(DataLoaderConfig(path_or_repo=str(sample_qa_path), limit=2)).load_qa()
    gold_ids = list(dict.fromkeys(r.doc_id for r in qa))
    assert len(gold_ids) == 2

    corpus_loader = DataLoader(DataLoaderConfig(path_or_repo=str(sample_corpus_path), limit=1))
    docs = corpus_loader.load_corpus(doc_ids=gold_ids)
    loaded_ids = {doc.id_ for doc in docs}
    assert set(gold_ids) <= loaded_ids
    assert len(docs) >= len(gold_ids)


def test_load_corpus_adds_filler_docs_when_limit_exceeds_gold(
    sample_corpus_path: Path,
) -> None:
    """Verify extra non-gold documents fill remaining slots up to limit."""
    gold_ids = ["doc_de_005"]
    docs = DataLoader(DataLoaderConfig(path_or_repo=str(sample_corpus_path), limit=4)).load_corpus(
        doc_ids=gold_ids
    )
    loaded_ids = [doc.id_ for doc in docs]
    assert loaded_ids[0] == "doc_de_005"
    assert len(docs) == 4
    assert set(loaded_ids) > set(gold_ids)


def test_load_corpus_missing_doc_ids_raises(sample_corpus_path: Path) -> None:
    """Verify a required doc_id that is absent from the corpus raises ValueError."""
    loader = DataLoader(DataLoaderConfig(path_or_repo=str(sample_corpus_path)))
    with pytest.raises(ValueError, match="missing 1/1 required doc_ids"):
        loader.load_corpus(doc_ids=["missing_doc"])


def test_load_qa_limit_and_doc_id_filter(sample_qa_path: Path) -> None:
    """Verify QA limit and gold-doc filtering are applied independently."""
    limited = DataLoader(DataLoaderConfig(path_or_repo=str(sample_qa_path), limit=2)).load_qa()
    assert len(limited) == 2

    filtered = DataLoader(DataLoaderConfig(path_or_repo=str(sample_qa_path))).load_qa(
        doc_ids=["doc_de_004"]
    )
    assert len(filtered) == 1
    assert filtered[0].query_id == "q_de_002"


def test_parent_doc_id_strips_chunk_suffix():
    """Verify parent_doc_id strips `::chunk_{n}` and leaves unsuffixed ids unchanged."""
    assert DataLoader.parent_doc_id("doc123::chunk_2") == "doc123"
    assert DataLoader.parent_doc_id("doc123") == "doc123"
    assert DataLoader.parent_doc_id("doc::with::colons") == "doc::with::colons"
    assert DataLoader.parent_doc_id("") == ""


def test_chunk_docs_preserves_parent_ids_and_unique_chunk_ids():
    """Verify chunk_docs keeps parent id_ / metadata.doc_id and unique chunk_id metadata."""

    text_a = " ".join(["alpha"] * 200)
    text_b = " ".join(["bravo"] * 200)
    docs = [
        Document(text=text_a, id_="doc_a", metadata={"source": "unit"}),
        Document(text=text_b, id_="doc_b"),
    ]
    chunks = DataLoader.chunk_docs(docs, ChunkingConfig(size=64, overlap=0))
    assert len(chunks) > len(docs)

    chunk_ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))
    for chunk in chunks:
        assert chunk.id_ in {"doc_a", "doc_b"}
        assert chunk.metadata["doc_id"] == chunk.id_
        assert chunk.metadata["chunk_id"].startswith(f"{chunk.id_}::chunk_")


def test_chunk_docs_empty_input():
    """Verify chunk_docs returns an empty list for empty input."""
    assert DataLoader.chunk_docs([]) == []


def test_convert_nodes_to_results_uses_parent_doc_id_not_chunk_id():
    """Verify retrieval results report parent doc_id even when chunk_id is present."""

    workflow = RAGWorkflow(config=RAGConfig())
    node = TextNode(
        text="chunk text",
        id_="ignored-node-id",
        metadata={"doc_id": "doc_a", "chunk_id": "doc_a::chunk_0"},
    )
    results = workflow.convert_nodes_to_results([NodeWithScore(node=node, score=0.9)])
    assert len(results) == 1
    assert results[0].doc_id == "doc_a"


def test_convert_nodes_to_results_strips_leaked_chunk_suffix():
    """Verify a leaked `::chunk_n` node id is stripped when metadata.doc_id is absent."""
    workflow = RAGWorkflow(config=RAGConfig())
    node = TextNode(text="x", id_="parent::chunk_3")
    results = workflow.convert_nodes_to_results([NodeWithScore(node=node, score=0.1)])
    assert results[0].doc_id == "parent"


def test_chunking_overlap_validation_skipped_for_semantic_and_hierarchical():
    """Verify overlap >= size is allowed for semantic and hierarchical strategies."""
    semantic = ChunkingConfig(strategy=ChunkingStrategy.SEMANTIC, size=64, overlap=64)
    assert semantic.strategy == ChunkingStrategy.SEMANTIC
    hierarchical = ChunkingConfig(strategy=ChunkingStrategy.HIERARCHICAL, size=64, overlap=64)
    assert hierarchical.resolved_hierarchical_chunk_sizes() == [256, 64, 64]
    recursive = ChunkingConfig(strategy=ChunkingStrategy.RECURSIVE, size=64, overlap=64)
    assert recursive.strategy == ChunkingStrategy.RECURSIVE


def test_chunking_overlap_validation_still_applies_to_sentence():
    """Verify sentence chunking still rejects overlap >= size."""
    with pytest.raises(ValueError, match="Chunk overlap"):
        ChunkingConfig(strategy=ChunkingStrategy.SENTENCE, size=64, overlap=64)


def test_hierarchical_chunking_rejected_with_graph_backend():
    """Verify hierarchical ingest raises when combined with graph retrieval."""
    cfg = RAGConfig()
    cfg.chunking.strategy = ChunkingStrategy.HIERARCHICAL
    cfg.retrieval.backend = RetrievalBackend.GRAPH
    workflow = RAGWorkflow(config=cfg)
    with pytest.raises(ValueError, match="Hierarchical chunking is not supported"):
        workflow._build_index([Document(text="hello world", id_="d1")])

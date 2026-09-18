"""Tests for dataset loader routing, configuration inference, and local parsing."""

from pathlib import Path

import pytest

from deepfix_rag.config.schema import DataLoaderConfig, DataPreset, DataSourceType
from deepfix_rag.data import (
    BundestagLoader,
    DataLoader,
    HuggingFaceLoader,
    LegalRagBenchLoader,
)

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

    data_loader = DataLoader()
    docs = data_loader.load_corpus(corpus_file)
    assert len(docs) == 1
    assert docs[0].id_ == "c1"
    assert docs[0].text == "Corpus document text."

    qa = data_loader.load_qa(DataLoaderConfig(path_or_repo=str(qa_file)))
    assert len(qa) == 1
    assert qa[0]["query"] == "Frage?"
    assert qa[0]["answer"] == "Antwort."


def test_sample_benchmark_fixtures_loading(sample_corpus_path: Path, sample_qa_path: Path):
    """Verify shared sample fixtures can be loaded via DataLoader."""
    data_loader = DataLoader()
    docs = data_loader.load_corpus(sample_corpus_path)
    assert len(docs) > 0
    assert docs[0].id_ is not None

    qa = data_loader.load_qa(DataLoaderConfig(path_or_repo=str(sample_qa_path)))
    assert len(qa) > 0
    assert "query" in qa[0]


def test_to_qrels_df_single_and_multi_doc_ids():
    """Verify to_qrels_df handles single doc_id, doc_ids list, and relevant_doc_ids list."""
    qa_records = [
        {"query_id": "q1", "doc_id": "d1"},
        {"query_id": "q2", "doc_ids": ["d2", "d3"]},
        {"query_id": "q3", "relevant_doc_ids": ["d4", "d5"]},
        {"query_id": "q4"},  # no doc_id
    ]
    df = DataLoader.to_qrels_df(qa_records)
    assert list(df.columns) == ["qid", "docno", "label"]
    assert len(df) == 5
    assert set(df["qid"]) == {"q1", "q2", "q3"}
    assert set(df["docno"]) == {"d1", "d2", "d3", "d4", "d5"}
    assert all(df["label"] == 1)


def test_to_qrels_df_empty():
    """Verify to_qrels_df returns typed empty DataFrame for empty inputs."""
    df = DataLoader.to_qrels_df([])
    assert list(df.columns) == ["qid", "docno", "label"]
    assert len(df) == 0


def test_load_qrels_from_sample_qa(sample_qa_path: Path):
    """Verify load_qrels produces valid PyTerrier qrels DataFrame from sample QA file."""
    data_loader = DataLoader()
    qrels = data_loader.load_qrels(sample_qa_path)
    assert len(qrels) == 3
    assert set(qrels["qid"]) == {"q_de_001", "q_de_002", "q_de_003"}
    assert set(qrels["docno"]) == {"doc_de_001", "doc_de_004", "doc_de_003"}
    assert all(qrels["label"] == 1)

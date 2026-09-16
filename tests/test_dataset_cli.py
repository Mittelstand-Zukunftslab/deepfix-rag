from pathlib import Path
import json
import pytest
from typer.testing import CliRunner

from deepfix_rag.main import app

runner = CliRunner()


def test_dataset_invalid_split(tmp_path: Path):
    """Verify that any split other than dev/test is explicitly rejected to prevent leakage."""
    result = runner.invoke(
        app,
        ["dataset", "load", "--name", "bundestag-qa", "--split", "train", "--data-root", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "split must be 'dev' or 'test'" in result.stdout


def test_dataset_load_live_hf_dev_split(tmp_path: Path):
    """Verify loading real records from live Hugging Face Hub with --limit into isolated directory."""
    result = runner.invoke(
        app,
        [
            "dataset",
            "load",
            "--name",
            "bundestag-qa",
            "--split",
            "dev",
            "--limit",
            "5",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Loading dataset 'bundestag-qa'" in result.stdout
    assert "isolated successfully for dev" in result.stdout

    target_dir = tmp_path / "bundestag-qa" / "dev"
    assert target_dir.exists()

    corpus_path = target_dir / "corpus.jsonl"
    qa_path = target_dir / "qa.jsonl"
    assert corpus_path.exists()
    assert qa_path.exists()

    # Verify actual data content from Hugging Face
    with open(corpus_path, "r", encoding="utf-8") as f:
        corpus_lines = [json.loads(line) for line in f if line.strip()]
    assert len(corpus_lines) > 0
    assert "doc_id" in corpus_lines[0]
    assert "text" in corpus_lines[0]
    assert len(corpus_lines[0]["text"]) > 0

    with open(qa_path, "r", encoding="utf-8") as f:
        qa_lines = [json.loads(line) for line in f if line.strip()]
    assert len(qa_lines) > 0
    assert "query_id" in qa_lines[0]
    assert "query" in qa_lines[0]
    assert len(qa_lines[0]["query"]) > 0


def test_dataset_list(tmp_path: Path):
    """Verify the list command displays locally downloaded datasets and splits without mocks."""
    # Pre-populate isolated data directory with real split directories
    dataset_dir = tmp_path / "bundestag-qa" / "dev"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "corpus.jsonl").write_text('{"doc_id": "1", "text": "pass"}\n', encoding="utf-8")

    result = runner.invoke(app, ["dataset", "list", "--data-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "bundestag-qa" in result.stdout
    assert "dev" in result.stdout


def test_dataset_generate_unanswerable(tmp_path: Path):
    """Verify synthetic unanswerable generation transforms real QA records and adds deflection responses."""
    # Create real input QA pairs
    input_dir = tmp_path / "dataset"
    input_dir.mkdir(parents=True, exist_ok=True)
    qa_file = input_dir / "qa.jsonl"
    sample_qas = [
        {"query_id": "q1", "query": "Was regelt § 315c StGB?", "answer": "Gefährdung des Straßenverkehrs."},
        {"query_id": "q2", "query": "Was regelt § 823 BGB?", "answer": "Schadensersatzpflicht."},
        {"query_id": "q3", "query": "Was regelt § 7 StVG?", "answer": "Haftung des Halters."},
        {"query_id": "q4", "query": "Was regelt § 17 StVG?", "answer": "Schadensverteilung."},
    ]
    with open(qa_file, "w", encoding="utf-8") as f:
        for q in sample_qas:
            f.write(json.dumps(q) + "\n")

    out_file = tmp_path / "unanswerable_out.jsonl"
    result = runner.invoke(
        app,
        [
            "dataset",
            "generate-unanswerable",
            "--dataset",
            str(input_dir),
            "--ratio",
            "0.5",
            "--output",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert "Generating synthetic unanswerable queries" in result.stdout
    assert out_file.exists()

    with open(out_file, "r", encoding="utf-8") as f:
        generated = [json.loads(line) for line in f if line.strip()]

    assert len(generated) == 4
    unanswerable = [r for r in generated if r.get("is_unanswerable")]
    assert len(unanswerable) == 2
    assert "not have enough information" in unanswerable[0]["answer"].lower()

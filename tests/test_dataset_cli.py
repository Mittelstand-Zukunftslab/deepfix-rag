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
        ["dataset", "load", "--name", "bundestag-qa", "--split", "invalid", "--data-root", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "split must be 'train'" in result.stdout


@pytest.mark.parametrize("dataset_name", ["bundestag-qa", "legal-rag-bench"])
def test_dataset_load_live_hf_dev_split(tmp_path: Path, dataset_name: str):
    """Verify loading real records from live Hugging Face Hub with --limit into isolated directory."""
    result = runner.invoke(
        app,
        [
            "dataset",
            "load",
            "--name",
            dataset_name,
            "--split",
            "train",
            "--limit",
            "5",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert f"Loading dataset '{dataset_name}'" in result.stdout
    assert "isolated successfully for train" in result.stdout

    target_dir = tmp_path / dataset_name / "train"
    assert target_dir.exists()

    corpus_path = target_dir / "corpus_5.jsonl"
    qa_path = target_dir / "qa_5.jsonl"
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
    for name in ["bundestag-qa", "legal-rag-bench"]:
        dataset_dir = tmp_path / name / "train"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        (dataset_dir / "corpus.jsonl").write_text('{"doc_id": "1", "text": "pass"}\n', encoding="utf-8")

    result = runner.invoke(app, ["dataset", "list", "--data-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "bundestag-qa" in result.stdout
    assert "legal-rag-bench" in result.stdout
    assert "train" in result.stdout

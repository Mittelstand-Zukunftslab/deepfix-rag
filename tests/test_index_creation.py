import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from deepfix_rag.main import app

runner = CliRunner()


def test_cli_index_cmd_missing_force_fails(tmp_path: Path):
    """Verify that indexing into an existing non-empty directory requires --force."""
    index_dir = tmp_path / "existing_index"
    index_dir.mkdir()
    (index_dir / "marker.txt").write_text("existing data", encoding="utf-8")

    sample_corpus = Path(__file__).parent / "data" / "sample_corpus.jsonl"
    config_path = Path("configs/default.yaml")

    result = runner.invoke(
        app,
        [
            "index",
            "--dataset",
            str(sample_corpus),
            "--config",
            str(config_path),
            "--index-dir",
            str(index_dir),
            "--json",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "already exists" in payload["error"]
    assert "--force" in payload["error"]


def test_cli_index_cmd_with_force_succeeds(tmp_path: Path):
    """Verify that --force overwrites existing index directory and creates LanceDB index on disk."""
    index_dir = tmp_path / "existing_index"
    index_dir.mkdir()
    (index_dir / "marker.txt").write_text("existing data", encoding="utf-8")

    sample_corpus = Path(__file__).parent / "data" / "sample_corpus.jsonl"
    config_path = Path("configs/default.yaml")

    result = runner.invoke(
        app,
        [
            "index",
            "--dataset",
            str(sample_corpus),
            "--config",
            str(config_path),
            "--index-dir",
            str(index_dir),
            "--force",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "index_path" in payload

    # Verify that marker.txt was deleted and LanceDB documents dataset was created
    assert not (index_dir / "marker.txt").exists()
    assert (index_dir / "documents.lance").exists()

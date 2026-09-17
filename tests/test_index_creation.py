"""Tests for CLI index command, directory validation, overwrite protection, and index persistence."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.main import app

pytestmark = [pytest.mark.cli, pytest.mark.integration]


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
    assert result.exit_code == EXIT_VALIDATION_ERROR or result.exit_code != 0
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

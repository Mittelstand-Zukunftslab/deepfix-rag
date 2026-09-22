"""Tests for DeepFix diagnostics engine, IR adapter, and CLI."""

import json
from pathlib import Path

from deepfix_core.models.api import APIResponse
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import DataLoaderConfig, DataPreset, RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.diagnostics.engine import run_diagnostics
from deepfix_rag.main import app


def _write_rag_config(path: Path, cfg: RAGConfig) -> Path:
    """Serialize RAGConfig to YAML at the given path."""
    path.write_text(cfg.to_yaml(), encoding="utf-8")
    return path


# =========================================================================
# CLI unit tests
# =========================================================================


@pytest.mark.cli
@pytest.mark.unit
def test_run_diagnostic_help(cli_runner: CliRunner) -> None:
    """Verify run-diagnostic help documents the supported options."""
    result = cli_runner.invoke(app, ["run-diagnostic", "--help"])
    assert result.exit_code == EXIT_SUCCESS
    for option in ("--dataset", "--config", "--server-url", "--limit", "--json"):
        assert option in result.stdout


@pytest.mark.cli
@pytest.mark.unit
def test_run_diagnostic_missing_config_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify missing --config with --json returns exit code 1 and error JSON."""
    missing = tmp_path / "does-not-exist.yaml"
    result = cli_runner.invoke(
        app,
        ["run-diagnostic", "--config", str(missing), "--json"],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR
    assert "not found" in payload["error"].lower()


@pytest.mark.cli
@pytest.mark.unit
def test_run_diagnostic_missing_config_text(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify missing --config without --json prints an error to stdout."""
    missing = tmp_path / "does-not-exist.yaml"
    result = cli_runner.invoke(app, ["run-diagnostic", "--config", str(missing)])
    assert result.exit_code == EXIT_VALIDATION_ERROR
    assert "Error:" in result.stdout
    assert "not found" in result.stdout.lower()


@pytest.mark.cli
@pytest.mark.unit
def test_run_diagnostic_missing_dataset_json(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """Verify a dataset directory with no QA file maps to JSON validation error."""
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb")
    cfg.retrieval.language = "german"
    cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
    config_path = _write_rag_config(tmp_path / "config.yaml", cfg)

    empty_dataset_dir = tmp_path / "empty_dataset"
    empty_dataset_dir.mkdir()

    result = cli_runner.invoke(
        app,
        [
            "run-diagnostic",
            "--config",
            str(config_path),
            "--dataset",
            str(empty_dataset_dir),
            "--limit",
            "2",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR
    assert "error" in payload


# =========================================================================
# Live Integration Tests
# =========================================================================


@pytest.mark.integration
def test_run_diagnostic_live_server(
    tmp_path: Path,
):
    """Verify that run_diagnostics successfully calls local DeepFix server and returns APIResponse."""
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb")
    cfg.retrieval.language = "german"
    cfg.data.path_or_repo = DataPreset.BUNDESTAG_QA
    cfg.data.limit = 20

    response = run_diagnostics(
        config=cfg,
        server_url=settings.DEEPFIX_SERVER_URL,
    )

    assert isinstance(response, APIResponse)
    assert response.agent_results is not None
    text_report = response.get_results_as_text()
    assert "SUMMARY STATISTICS" in text_report


@pytest.mark.cli
@pytest.mark.integration
def test_run_diagnostic_cli_live_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify run-diagnostic --json succeeds against the local DeepFix server."""
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb")
    cfg.retrieval.language = "german"
    cfg.data.path_or_repo = DataPreset.BUNDESTAG_QA
    cfg.data.limit = 20
    config_path = _write_rag_config(tmp_path / "config.yaml", cfg)

    result = cli_runner.invoke(
        app,
        [
            "run-diagnostic",
            "--config",
            str(config_path),
            "--server-url",
            settings.DEEPFIX_SERVER_URL,
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.stdout
    payload = json.loads(result.stdout)
    response = APIResponse.model_validate(payload)
    assert response.agent_results is not None

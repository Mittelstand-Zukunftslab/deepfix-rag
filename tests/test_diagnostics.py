"""Tests for DeepFix diagnostics engine, IR adapter, and CLI."""

import json
from pathlib import Path

from deepfix_core.models.api import APIResponse
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import DataPreset, DataSourceType, RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.diagnostics.engine import run_diagnostics
from deepfix_rag.main import app


def _write_rag_config(path: Path, cfg: RAGConfig) -> Path:
    """Serialize RAGConfig to YAML at the given path."""
    path.write_text(cfg.to_yaml(), encoding="utf-8")
    return path


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


@pytest.mark.integration
def test_run_diagnostics_reinfers_source_type_for_dataset_override(tmp_path: Path) -> None:
    """A dataset override must reach the loader with a re-inferred source type."""
    dataset_dir = tmp_path / "workspace"
    dataset_dir.mkdir()
    (dataset_dir / "qa.jsonl").write_text("", encoding="utf-8")
    cfg = RAGConfig()
    assert cfg.data.source_type == DataSourceType.HUGGINGFACE
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb")

    with pytest.raises(ValueError, match="returned no QA pairs") as raised:
        run_diagnostics(dataset=dataset_dir, config=cfg)

    assert "Unknown source type" not in str(raised.value)


@pytest.mark.cli
@pytest.mark.integration
def test_run_diagnostic_cli_dataset_override_is_not_an_unknown_source_type(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """run-diagnostic --dataset <dir> gets past loader dispatch (exit 1 is the empty QA file)."""
    dataset_dir = tmp_path / "workspace"
    dataset_dir.mkdir()
    (dataset_dir / "qa.jsonl").write_text("", encoding="utf-8")

    result = cli_runner.invoke(app, ["run-diagnostic", "--dataset", str(dataset_dir), "--json"])

    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert "returned no QA pairs" in payload["error"]
    assert "Unknown source type" not in payload["error"]

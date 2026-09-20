"""Tests for DeepFix diagnostics engine, IR adapter, and CLI."""

import json
from pathlib import Path

from deepfix_core.models.api import APIResponse
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_RUNTIME_ERROR, EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.data import DataLoader
from deepfix_rag.diagnostics.engine import run_diagnostics
from deepfix_rag.evaluation.runner import CaseResult
from deepfix_rag.main import app


@pytest.fixture
def sample_cases() -> list[CaseResult]:
    """Provide a reproducible list of CaseResult objects for unit tests."""
    return [
        CaseResult(
            index=0,
            query_id="q1",
            question="What is the legal definition of negligence?",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1", "doc_2"],
            retrieved_scores=[0.95, 0.45],
        ),
        CaseResult(
            index=1,
            query_id="q2",
            question="What is the limitation period for contractual claims?",
            ground_truth_doc_ids=["doc_3"],
            retrieved_doc_ids=["doc_4", "doc_3"],
            retrieved_scores=[0.80, 0.70],
        ),
        CaseResult(
            index=2,
            query_id="q3",
            question="How does contributory negligence affect damages?",
            ground_truth_doc_ids=["doc_2"],
            retrieved_doc_ids=["doc_5"],
            retrieved_scores=[0.60],
        ),
        CaseResult(
            index=3,
            query_id="q4",
            question="When can punitive damages be awarded?",
            ground_truth_doc_ids=["doc_5"],
            retrieved_doc_ids=["doc_5", "doc_1"],
            retrieved_scores=[0.90, 0.30],
        ),
    ]


@pytest.fixture
def sample_corpus() -> dict[str, str]:
    """Provide a dictionary of document text keyed by document ID from Bundestag corpus."""
    corpus_cfg = DataLoaderConfig(path_or_repo="wirthual/dip-bundestag", limit=50)
    corpus_docs = DataLoader().load_corpus(corpus_cfg)
    return {doc.id_: doc.text for doc in corpus_docs}


# =========================================================================
# CLI Validation Tests
# =========================================================================


@pytest.mark.cli
def test_cli_diagnostic_missing_config_fails(cli_runner: CliRunner, tmp_path: Path):
    """Verify exit code 1 when configuration file does not exist."""
    missing_config = tmp_path / "non_existent_config.yaml"
    result = cli_runner.invoke(
        app,
        ["run-diagnostic", "--config", str(missing_config)],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    assert "Config file not found" in result.output


def _dump_yaml(cfg: RAGConfig) -> str:
    """Serialize RAGConfig to YAML string."""
    import yaml

    return yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False)


@pytest.mark.cli
def test_cli_diagnostic_server_unreachable_exit_code_2(
    cli_runner: CliRunner,
    sample_qa_path: Path,
    sample_rag_config: RAGConfig,
    isolated_mlflow_env: str,
    tmp_path: Path,
):
    """Verify exit code 2 when DeepFix server is unreachable (fail-fast policy)."""
    # Write sample config to temporary file
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(_dump_yaml(sample_rag_config), encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "run-diagnostic",
            "--dataset",
            str(sample_qa_path),
            "--config",
            str(config_file),
            "--server-url",
            "http://localhost:59999",  # Non-existent port
            "--limit",
            "2",
        ],
    )
    assert result.exit_code == EXIT_RUNTIME_ERROR
    assert "Failed to communicate with DeepFix server" in result.output


# =========================================================================
# Live Integration Tests
# =========================================================================


@pytest.mark.integration
def test_run_diagnostics_live_server(
    sample_qa_path: Path,
    sample_corpus_path: Path,
    isolated_mlflow_env: str,
    tmp_path: Path,
):
    """Verify that run_diagnostics successfully calls local DeepFix server and returns APIResponse."""
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb")
    cfg.retrieval.language = "german"
    cfg.data.path_or_repo = str(sample_corpus_path)
    cfg.data.limit = 4

    response = run_diagnostics(
        dataset=str(sample_qa_path),
        config=cfg,
        server_url="http://localhost:8844",
        limit=4,
    )

    assert isinstance(response, APIResponse)
    assert response.agent_results is not None
    text_report = response.get_results_as_text()
    assert "SUMMARY STATISTICS" in text_report


@pytest.mark.cli
def test_cli_diagnostic_live_json_output(
    cli_runner: CliRunner,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    isolated_mlflow_env: str,
    tmp_path: Path,
):
    """Verify deepfix run-diagnostic --json emits valid APIResponse JSON to stdout with exit code 0."""
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(tmp_path / "lancedb_cli")
    cfg.retrieval.language = "german"
    cfg.data.path_or_repo = str(sample_corpus_path)
    cfg.data.limit = 4

    config_file = tmp_path / "cli_config.yaml"
    config_file.write_text(_dump_yaml(cfg), encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "run-diagnostic",
            "--dataset",
            str(sample_qa_path),
            "--config",
            str(config_file),
            "--server-url",
            "http://localhost:8844",
            "--limit",
            "4",
            "--json",
        ],
    )

    assert result.exit_code == EXIT_SUCCESS, f"Command failed: {result.output}"
    # Parse JSON from stdout
    data = json.loads(result.stdout)
    assert "agent_results" in data

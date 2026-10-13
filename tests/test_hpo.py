"""Unit, integration, and CLI tests for Multi-Paradigm Hyperparameter Optimization (US-011)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.hpo import (
    HPOConfig,
)
from deepfix_rag.config.schema import (
    RAGConfig,
    RetrievalBackend,
)
from deepfix_rag.hpo import HPOResult, HyperparameterOpt
from deepfix_rag.main import app


@pytest.mark.integration
def test_hyperparameter_opt_retrieval_only_run(
    populated_sample_index_dir: Path,
    sample_qa_path: Path,
    tmp_path: Path,
):
    """Run real Optuna HPO multi-objective study on deterministic local fixtures."""
    cfg = RAGConfig()
    cfg.retrieval.backend = RetrievalBackend.HYBRID
    cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.retrieval_only = True
    cfg.data.path_or_repo = str(sample_qa_path)
    cfg.data.limit = 2

    hpo = HyperparameterOpt(
        base_config=cfg,
        study_name="test_hpo_study",
        tune_chunking=False,
    )

    result = hpo.optimize(
        n_trials=2,
        dataset_path=str(sample_qa_path),
        limit=2,
    )

    assert isinstance(result, HPOResult)
    assert len(result.study.trials) == 2
    assert result.best_trial is not None
    assert result.best_config is not None
    assert "retrieval.dense_weight" in result.best_trial.params

    # Verify DataFrame and dict serialization
    df = result.to_dataframe()
    assert not df.empty
    assert "trial_number" in df.columns

    res_dict = result.to_dict()
    assert res_dict["total_trials"] == 2
    assert res_dict["completed_trials"] == 2

    # Verify YAML export
    yaml_out = tmp_path / "best_optimized_config.yaml"
    saved = result.export_best_config(yaml_out)
    assert saved.exists()
    content = saved.read_text(encoding="utf-8")
    assert "dense_weight" in content


# =========================================================================
# CLI Tests: Typer hpo Command
# =========================================================================


@pytest.mark.cli
def test_cli_hpo_help(cli_runner: CliRunner):
    """Verify deepfix hpo help displays required flags."""
    result = cli_runner.invoke(app, ["hpo", "--help"])
    assert result.exit_code == EXIT_SUCCESS
    assert "--config" in result.stdout
    assert "--n-trials" in result.stdout
    assert "--storage-uri" in result.stdout
    assert "--paradigm" in result.stdout
    assert "--retrieval-only" in result.stdout


@pytest.mark.cli
def test_cli_hpo_missing_config_fails(cli_runner: CliRunner, tmp_path: Path):
    """Verify deepfix hpo exits with validation error on missing config."""
    fake_config = tmp_path / "nonexistent.yaml"
    result = cli_runner.invoke(app, ["hpo", "--config", str(fake_config), "--json"])
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"


@pytest.mark.cli
def test_cli_hpo_json_execution(
    cli_runner: CliRunner,
    populated_sample_index_dir: Path,
    sample_qa_path: Path,
    tmp_path: Path,
):
    """Verify deepfix hpo runs end-to-end and emits valid JSON output to stdout."""
    # Prepare temporary base config pointing to pre-populated LanceDB index
    base_cfg = RAGConfig()
    base_cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
    base_cfg.retrieval.load_if_exists = True
    base_cfg.retrieval_only = True
    base_cfg.data.path_or_repo = str(sample_qa_path)
    base_cfg.data.limit = 2

    cfg_path = tmp_path / "test_hpo_base.yaml"
    import yaml

    cfg_path.write_text(yaml.safe_dump(base_cfg.model_dump(mode="json")), encoding="utf-8")

    out_yaml = tmp_path / "exported_best.yaml"

    result = cli_runner.invoke(
        app,
        [
            "hpo",
            "--config",
            str(cfg_path),
            "--n-trials",
            "2",
            "--retrieval-only",
            "--limit",
            "2",
            "--output",
            str(out_yaml),
            "--json",
        ],
    )

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["total_trials"] == 2
    assert payload["completed_trials"] == 2
    assert "best_trial" in payload
    assert out_yaml.exists()


@pytest.mark.cli
def test_cli_hpo_declarative_execution(
    cli_runner: CliRunner,
    populated_sample_index_dir: Path,
    sample_qa_path: Path,
    tmp_path: Path,
):
    """Verify deepfix hpo executes declaratively from an HPOConfig YAML file."""

    base_cfg = RAGConfig()
    base_cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
    base_cfg.retrieval.load_if_exists = True
    base_cfg.retrieval_only = True

    base_yaml_path = tmp_path / "base.yaml"
    base_yaml_path.write_text(yaml.safe_dump(base_cfg.model_dump(mode="json")), encoding="utf-8")

    hpo_cfg = HPOConfig(
        base_config="base.yaml",
        study_name="cli_declarative_study",
        storage_uri="sqlite:///:memory:",
        n_trials=2,
        retrieval_only=True,
        dataset=str(sample_qa_path),
        limit=2,
    )
    hpo_yaml_path = tmp_path / "hpo.yaml"
    hpo_yaml_path.write_text(
        yaml.safe_dump(hpo_cfg.model_dump(mode="json")),
        encoding="utf-8",
    )

    out_yaml = tmp_path / "best_declarative.yaml"

    result = cli_runner.invoke(
        app,
        [
            "hpo",
            "--config",
            str(hpo_yaml_path),
            "--output",
            str(out_yaml),
            "--json",
        ],
    )

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["study_name"] == "cli_declarative_study"
    assert payload["total_trials"] == 2
    assert payload["completed_trials"] == 2
    assert "best_trial" in payload
    assert out_yaml.exists()

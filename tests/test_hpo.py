"""Unit, integration, and CLI tests for Multi-Paradigm Hyperparameter Optimization (US-011)."""

import json
from pathlib import Path

import optuna
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.hpo import (
    DenseSearchSpace,
    GraphSearchSpace,
    HPOConfig,
    HPOMetricConfig,
    HPOSearchSpace,
    HybridSearchSpace,
    load_hpo_config,
)
from deepfix_rag.config.mutator import apply_mutation
from deepfix_rag.config.schema import (
    FusionMode,
    RAGConfig,
    RetrievalBackend,
    SimilarityMetric,
)
from deepfix_rag.hpo import HPOResult, HyperparameterOpt
from deepfix_rag.main import app

# =========================================================================
# Unit Tests: Search Spaces & Pydantic Schema Validation
# =========================================================================


@pytest.mark.unit
def test_dense_search_space_defaults():
    """Verify Dense search space satisfies US-011 boundary requirements."""
    space = DenseSearchSpace()
    assert space.chunk_size_min == 128
    assert space.chunk_size_max == 2048
    assert space.chunk_overlap_min == 0
    assert space.chunk_overlap_max == 256
    assert space.top_k_min == 1
    assert space.top_k_max == 50
    assert SimilarityMetric.COSINE in space.similarity_metrics


@pytest.mark.unit
def test_hybrid_search_space_defaults():
    """Verify Hybrid search space satisfies US-011 boundary requirements."""
    space = HybridSearchSpace()
    assert space.bm25_k1_min == 0.5
    assert space.bm25_k1_max == 3.0
    assert space.bm25_b_min == 0.1
    assert space.bm25_b_max == 1.0
    assert space.dense_weight_min == 0.0
    assert space.dense_weight_max == 1.0
    assert FusionMode.RECIPROCAL_RANK in space.fusion_modes


@pytest.mark.unit
def test_graph_search_space_defaults():
    """Verify Graph search space satisfies US-011 boundary requirements."""
    space = GraphSearchSpace()
    assert "default" in space.extraction_schemas
    assert space.max_paths_per_chunk_min == 1
    assert space.max_paths_per_chunk_max == 10
    assert space.traversal_hops_min == 1
    assert space.traversal_hops_max == 3
    assert space.dense_weight_min == 0.0
    assert space.dense_weight_max == 1.0
    assert FusionMode.RECIPROCAL_RANK in space.fusion_modes


@pytest.mark.unit
def test_invalid_combinations_rejected():
    """Verify that invalid parameter combinations are rejected by Pydantic validation."""
    # Invalid ChunkingConfig: overlap >= size
    with pytest.raises(ValueError, match="Chunk overlap .* must be strictly less than chunk size"):
        apply_mutation(RAGConfig(), {"chunking.size": 256, "chunking.overlap": 256})

    # Invalid bounds on search spaces
    with pytest.raises(ValueError):
        DenseSearchSpace(chunk_size_min=2000, chunk_size_max=1000)

    with pytest.raises(ValueError):
        HybridSearchSpace(bm25_k1_min=4.0, bm25_k1_max=2.0)

    with pytest.raises(ValueError):
        GraphSearchSpace(dense_weight_min=0.8, dense_weight_max=0.2)

    # Invalid graph fusion weights (negative)
    with pytest.raises(ValueError, match="Graph fusion weights must be non-negative"):
        apply_mutation(RAGConfig(), {"retrieval.graph_fusion_weights": [-0.5, 1.5]})


@pytest.mark.unit
def test_search_space_suggest_parameters_dense():
    """Verify Optuna suggestion generation for dense paradigm."""
    hpo_space = HPOSearchSpace()
    study = optuna.create_study(direction="maximize")
    trial = study.ask()

    params = hpo_space.suggest_parameters(
        trial, paradigm=RetrievalBackend.DENSE, tune_chunking=True
    )
    assert "retrieval.top_k" in params
    assert "retrieval.similarity_metric" in params
    assert "chunking.size" in params
    assert "chunking.overlap" in params
    assert params["chunking.overlap"] < params["chunking.size"]

    # Applying to base RAGConfig must succeed without validation errors
    cfg = apply_mutation(RAGConfig(), params)
    assert cfg.retrieval.top_k == params["retrieval.top_k"]
    assert cfg.chunking.size == params["chunking.size"]


@pytest.mark.unit
def test_search_space_suggest_parameters_hybrid():
    """Verify Optuna suggestion generation for hybrid paradigm."""
    hpo_space = HPOSearchSpace()
    study = optuna.create_study(direction="maximize")
    trial = study.ask()

    params = hpo_space.suggest_parameters(
        trial, paradigm=RetrievalBackend.HYBRID, tune_chunking=False
    )
    assert "retrieval.bm25_k1" in params
    assert "retrieval.bm25_b" in params
    assert "retrieval.fusion_mode" in params
    assert "retrieval.dense_weight" in params
    assert "retrieval.bm25_weight" in params

    cfg = apply_mutation(RAGConfig(), params)
    assert cfg.retrieval.bm25_k1 == params["retrieval.bm25_k1"]
    assert cfg.retrieval.fusion_mode == params["retrieval.fusion_mode"]
    assert cfg.retrieval.dense_weight == params["retrieval.dense_weight"]


@pytest.mark.unit
def test_search_space_suggest_parameters_graph():
    """Verify Optuna suggestion generation for graph paradigm."""
    hpo_space = HPOSearchSpace()
    study = optuna.create_study(direction="maximize")
    trial = study.ask()

    params = hpo_space.suggest_parameters(
        trial, paradigm=RetrievalBackend.GRAPH, tune_chunking=False
    )
    assert "retrieval.graph_extraction_schema" in params
    assert "retrieval.graph_max_paths_per_chunk" in params
    assert "retrieval.graph_traversal_hops" in params
    assert "retrieval.fusion_mode" in params
    assert "retrieval.dense_weight" in params
    assert "retrieval.graph_weight" in params
    assert "retrieval.graph_fusion_weights" in params

    cfg = apply_mutation(RAGConfig(), params)
    assert cfg.retrieval.graph_traversal_hops == params["retrieval.graph_traversal_hops"]
    assert cfg.retrieval.dense_weight == params["retrieval.dense_weight"]
    assert cfg.retrieval.graph_weight == params["retrieval.graph_weight"]
    assert len(cfg.retrieval.graph_fusion_weights) == 2


@pytest.mark.unit
def test_search_space_suggest_parameters_mixed():
    """Verify Optuna suggestion generation for mixed (Dense+BM25+Graph) paradigm."""
    hpo_space = HPOSearchSpace()
    study = optuna.create_study(direction="maximize")
    trial = study.ask()

    params = hpo_space.suggest_parameters(
        trial, paradigm=RetrievalBackend.MIXED, tune_chunking=False
    )
    assert "retrieval.top_k" in params
    assert "retrieval.bm25_k1" in params
    assert "retrieval.bm25_b" in params
    assert "retrieval.graph_extraction_schema" in params
    assert "retrieval.graph_traversal_hops" in params
    assert "retrieval.dense_weight" in params
    assert "retrieval.bm25_weight" in params
    assert "retrieval.graph_weight" in params
    assert "retrieval.fusion_mode" in params

    cfg = apply_mutation(RAGConfig(), params)
    assert cfg.retrieval.bm25_k1 == params["retrieval.bm25_k1"]
    assert cfg.retrieval.graph_traversal_hops == params["retrieval.graph_traversal_hops"]
    assert cfg.retrieval.dense_weight == params["retrieval.dense_weight"]
    assert cfg.retrieval.bm25_weight == params["retrieval.bm25_weight"]
    assert cfg.retrieval.graph_weight == params["retrieval.graph_weight"]


@pytest.mark.unit
def test_hpo_metric_config_and_alias_resolution():
    """Verify metrics and lookup aliases configured in HPO schema."""
    hpo_cfg = HPOConfig(
        metrics=HPOMetricConfig(
            targets=["recall", "faithfulness"],
            retrieval_only_targets=["recall", "ndcg"],
            aliases={
                "recall": ["custom_recall_k", "canonical_recall"],
                "ndcg": ["custom_ndcg_k"],
            },
        )
    )

    assert "recall" in hpo_cfg.metrics.targets
    assert "custom_recall_k" in hpo_cfg.metrics.aliases["recall"]

    # Test alias extraction using configured aliases
    metrics_payload = {
        "custom_recall_k": 0.88,
        "custom_ndcg_k": 0.92,
        "nDCG@7": 0.77,
        "R@7": 0.85,
    }

    val_recall = HyperparameterOpt.extract_metric(
        metrics_payload, "recall", aliases_map=hpo_cfg.metrics.aliases
    )
    val_ndcg = HyperparameterOpt.extract_metric(
        metrics_payload, "ndcg", aliases_map=hpo_cfg.metrics.aliases
    )
    val_missing = HyperparameterOpt.extract_metric(
        metrics_payload, "faithfulness", aliases_map=hpo_cfg.metrics.aliases
    )

    assert val_recall == 0.88
    assert val_ndcg == 0.92
    assert val_missing == 0.0

    # Test dynamic resolution by cutoff_k without custom aliases
    val_dyn_ndcg = HyperparameterOpt.extract_metric(metrics_payload, "ndcg", cutoff_k=7)
    val_dyn_recall = HyperparameterOpt.extract_metric(metrics_payload, "recall", cutoff_k=7)
    assert val_dyn_ndcg == 0.77
    assert val_dyn_recall == 0.85


@pytest.mark.unit
def test_hpo_config_defaults():
    """Verify HPOConfig defaults and structure."""
    cfg = HPOConfig()
    assert cfg.version == "1.0"
    assert cfg.base_config == "configs/default.yaml"
    assert cfg.storage_uri == "sqlite:///:memory:"
    assert cfg.n_trials == 10
    assert cfg.tune_chunking is False
    assert cfg.retrieval_only is False
    assert cfg.dataset is None
    assert cfg.split is None
    assert cfg.limit is None
    assert cfg.output is None
    assert isinstance(cfg.metrics, HPOMetricConfig)
    assert isinstance(cfg.search_space, HPOSearchSpace)


@pytest.mark.unit
def test_hpo_config_resolve_base_config_from_path(default_config_path: Path):
    """Verify resolve_base_config resolves RAGConfig from file path."""
    cfg = HPOConfig(base_config=str(default_config_path))
    rag_cfg = cfg.resolve_base_config()
    assert isinstance(rag_cfg, RAGConfig)
    assert rag_cfg.retrieval.backend == RetrievalBackend.HYBRID


@pytest.mark.unit
def test_hpo_config_resolve_base_config_from_inline_model():
    """Verify resolve_base_config returns inline RAGConfig directly."""
    base_rag = RAGConfig(retrieval_only=True)
    cfg = HPOConfig(base_config=base_rag)
    resolved = cfg.resolve_base_config()
    assert resolved is base_rag
    assert resolved.retrieval_only is True


@pytest.mark.unit
def test_hpo_config_resolve_base_config_from_dict():
    """Verify resolve_base_config validates dictionary as RAGConfig."""
    cfg = HPOConfig(base_config={"retrieval_only": True})
    resolved = cfg.resolve_base_config()
    assert isinstance(resolved, RAGConfig)
    assert resolved.retrieval_only is True


@pytest.mark.unit
def test_hpo_config_resolve_base_config_missing_file_raises():
    """Verify resolve_base_config raises FileNotFoundError on missing path."""
    cfg = HPOConfig(base_config="nonexistent_base_path.yaml")
    with pytest.raises(FileNotFoundError):
        cfg.resolve_base_config()


@pytest.mark.unit
def test_load_hpo_config_declarative_yaml():
    """Verify load_hpo_config loads repository configs/hpo.yaml."""
    hpo_path = Path("configs/hpo.yaml")
    assert hpo_path.exists()
    hpo_cfg = load_hpo_config(hpo_path)
    assert isinstance(hpo_cfg, HPOConfig)
    assert hpo_cfg.study_name == "deepfix_hpo_study"
    assert hpo_cfg.n_trials == 10
    assert hpo_cfg.search_space.hybrid.bm25_k1_min == 0.5


@pytest.mark.unit
def test_load_hpo_config_pure_rag_fallback(default_config_path: Path):
    """Verify load_hpo_config gracefully wraps pure RAGConfig YAML files."""
    hpo_cfg = load_hpo_config(default_config_path)
    assert isinstance(hpo_cfg, HPOConfig)
    assert isinstance(hpo_cfg.base_config, RAGConfig)
    assert hpo_cfg.base_config.retrieval.backend == RetrievalBackend.HYBRID


@pytest.mark.unit
def test_load_hpo_config_missing_file_raises():
    """Verify load_hpo_config raises FileNotFoundError when file does not exist."""
    with pytest.raises(FileNotFoundError):
        load_hpo_config("nonexistent_hpo.yaml")


# =========================================================================
# Integration Tests: HyperparameterOpt Execution (No Mocks)
# =========================================================================


@pytest.mark.integration
def test_hyperparameter_opt_retrieval_only_run(
    populated_sample_index_dir: Path,
    sample_qa_path: Path,
    isolated_mlflow_env: str,
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
        workspace="dev",
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
    default_config_path: Path,
    isolated_mlflow_env: str,
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
    isolated_mlflow_env: str,
    tmp_path: Path,
):
    """Verify deepfix hpo executes declaratively from an HPOConfig YAML file."""
    import yaml

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

"""Tests for Evaluation Runner, Deflection Detection, Metric Computation, and Live Pipeline Scoring."""

from pathlib import Path

import mlflow
import pytest

from deepfix_rag.config.schema import (
    DataLoaderConfig,
    GenerationMetricType,
    RAGConfig,
)
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.runner import EvaluationSummary, EvaluationTracker, run_evaluation


# =========================================================================
# 4. Live End-to-End Evaluation Tests
# =========================================================================
@pytest.mark.integration
@pytest.mark.e2e
class TestRunEvaluationLive:
    """Live integration and end-to-end evaluation tests using live judge and benchmark data."""

    @pytest.mark.asyncio
    async def test_run_evaluation_live_full_flow(
        self,
        sample_qa_path: Path,
        sample_corpus_path: Path,
        populated_sample_index_dir: Path,
    ):
        """Verify live run_evaluation execution on sample QA dataset with real judge and index."""
        assert settings.LLM_API_KEY, "LLM_API_KEY must be configured for live evaluation tests."

        cfg = RAGConfig()
        cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
        cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
        cfg.retrieval.load_if_exists = True
        cfg.retrieval.top_k = 3
        cfg.rerank.enabled = False
        cfg.eval.generation_metrics = [GenerationMetricType.FAITHFULNESS]
        cfg.eval.concurrency = 2

        results = await run_evaluation(
            dataset_path=sample_qa_path,
            config=cfg,
            run_name="live_eval_test",
        )

        assert results["total"] == 3
        assert results["scored"] == 3
        assert results["errors"] == 0
        assert "faithfulness" in results
        assert isinstance(results["faithfulness"], float)
        assert len(results["rows"]) == 3

        first_row = results["rows"][0]
        assert first_row["question"] != ""
        assert first_row["answer"] is not None
        assert first_row["faithfulness"] is not None

    @pytest.mark.asyncio
    async def test_run_evaluation_limit_slicing(
        self,
        sample_qa_path: Path,
        sample_corpus_path: Path,
        populated_sample_index_dir: Path,
    ):
        """Verify target limit restricts evaluation to exact number of questions."""
        cfg = RAGConfig()
        cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path), limit=1)
        cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
        cfg.retrieval.load_if_exists = True
        cfg.rerank.enabled = False
        cfg.eval.generation_metrics = [GenerationMetricType.FAITHFULNESS]

        results = await run_evaluation(
            dataset_path=sample_qa_path,
            config=cfg,
        )

        assert results["total"] == 1
        assert results["scored"] == 1
        assert len(results["rows"]) == 1


# =========================================================================
# 5. MLflow run id on the summary
# =========================================================================
@pytest.fixture
def mlflow_sqlite_experiment(tmp_path: Path):
    """Throwaway sqlite MLflow store with the experiment the tracker refuses to create."""
    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.create_experiment(settings.MLFLOW_DEV_RAG_EXPERIMENT)
    yield settings.MLFLOW_DEV_RAG_EXPERIMENT
    mlflow.set_tracking_uri(previous_uri)


@pytest.mark.unit
def test_summary_mlflow_run_id_defaults_to_none_and_is_in_to_dict():
    """An unpersisted summary reports mlflow_run_id as null and still compares as a dict."""
    summary = EvaluationSummary(total=1, scored=1, errors=0, metrics={"recall": 1.0})

    assert summary.mlflow_run_id is None
    assert summary.to_dict()["mlflow_run_id"] is None
    assert summary["recall"] == 1.0

    summary.mlflow_run_id = "abc123"
    assert summary["mlflow_run_id"] == "abc123"


@pytest.mark.integration
def test_tracker_log_run_records_run_id_and_logs_only_numeric_metrics(
    mlflow_sqlite_experiment: str,
):
    """log_run stores the active MLflow run id on the summary without logging it as a metric."""
    tracker = EvaluationTracker(experiment_name=mlflow_sqlite_experiment)
    tracker.setup()
    summary = EvaluationSummary(
        total=2,
        scored=2,
        errors=0,
        metrics={"recall": 0.5, "ndcg": 0.25},
        rows=[{"index": 0, "question": "q"}, {"index": 1, "question": "r"}],
    )

    tracker.log_run(run_name="run_id_check", params={"top_k": 3}, summary=summary)

    assert summary.mlflow_run_id
    logged = mlflow.get_run(summary.mlflow_run_id)
    assert logged.info.run_name == "run_id_check"
    assert logged.data.metrics["recall"] == pytest.approx(0.5)
    assert logged.data.metrics["ndcg"] == pytest.approx(0.25)
    assert "mlflow_run_id" not in logged.data.metrics


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_evaluation_returns_mlflow_run_id_only_when_persisting(
    mlflow_sqlite_experiment: str,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    populated_sample_index_dir: Path,
):
    """Persisted runs carry the MLflow run id; persist=False runs carry None."""
    cfg = RAGConfig()
    cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
    cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.rerank.enabled = False

    persisted = await run_evaluation(dataset_path=sample_qa_path, config=cfg, retrieval_only=True)
    unpersisted = await run_evaluation(
        dataset_path=sample_qa_path, config=cfg, retrieval_only=True, persist=False
    )

    assert persisted.errors == 0
    assert mlflow.get_run(persisted.mlflow_run_id).info.run_id == persisted.mlflow_run_id
    assert persisted["mlflow_run_id"] == persisted.mlflow_run_id
    assert unpersisted.mlflow_run_id is None
    assert unpersisted.to_dict()["mlflow_run_id"] is None

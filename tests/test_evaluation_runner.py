"""Tests for Evaluation Runner, Deflection Detection, Metric Computation, and Live Pipeline Scoring."""

from pathlib import Path

import pytest

from deepfix_rag.config.schema import (
    DataLoaderConfig,
    GenerationMetricType,
    RAGConfig,
)
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.runner import run_evaluation


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

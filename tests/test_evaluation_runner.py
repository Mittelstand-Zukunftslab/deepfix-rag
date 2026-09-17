"""Tests for Evaluation Runner, Deflection Detection, Metric Computation, and Live Pipeline Scoring."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from deepeval.test_case import LLMTestCase
import pytest

from deepfix_rag.config.schema import DataLoaderConfig, GenerationMetricType, RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.evaluation.runner import (
    DEFLECTION_PHRASES,
    _measure_one_metric,
    _run_one_case,
    count_tokens,
    is_deflection,
    make_metrics,
    run_evaluation,
    setup_mlflow,
)


# =========================================================================
# 1. Pure Helper Unit Tests
# =========================================================================
@pytest.mark.unit
class TestRunnerHelpers:
    """Unit tests for token counting, deflection heuristics, metrics builder, and MLflow setup."""

    def test_count_tokens(self):
        """Verify token counts for standard text, empty strings, and null cases."""
        assert count_tokens("Hallo Welt") > 0
        assert count_tokens("") == 0
        assert count_tokens(None) == 0

    def test_is_deflection_positive_cases(self):
        """Verify all declared deflection phrases are accurately detected."""
        for phrase in DEFLECTION_PHRASES:
            answer = f"Leider {phrase} im Dokument."
            assert is_deflection(answer) is True, f"Failed to detect deflection phrase: {phrase}"
            assert is_deflection(phrase.upper()) is True, (
                f"Failed case-insensitivity on phrase: {phrase}"
            )

    def test_is_deflection_negative_cases(self):
        """Verify standard generative answers do not trigger false positive deflections."""
        valid_answers = [
            "Die Gefährdungshaftung nach § 7 StVG setzt voraus, dass bei dem Betrieb ein Schaden entsteht.",
            "Gemäß § 315c StGB wird bestraft, wer im Straßenverkehr ein Fahrzeug führt.",
            "Der Anspruch aus § 823 Abs. 1 BGB verlangt ein Verschulden.",
        ]
        for ans in valid_answers:
            assert is_deflection(ans) is False

    def test_make_metrics(self):
        """Verify metric instantiation with thresholds and ProxyJudgeLLM."""
        judge = ProxyJudgeLLM(
            model="test-judge",
            api_base="https://example.com/v1",
            api_key="test-key",
        )
        metrics = make_metrics(
            generation_metrics=[
                GenerationMetricType.FAITHFULNESS,
                GenerationMetricType.ANSWER_RELEVANCY,
            ],
            async_mode=True,
            threshold=0.7,
            judge=judge,
        )
        assert GenerationMetricType.FAITHFULNESS in metrics
        assert GenerationMetricType.ANSWER_RELEVANCY in metrics
        assert metrics[GenerationMetricType.FAITHFULNESS].threshold == 0.7

    def test_setup_mlflow_missing_experiment_raises(self, monkeypatch: pytest.MonkeyPatch):
        """Verify setup_mlflow raises RuntimeError when configured experiment is missing."""
        import mlflow

        monkeypatch.setattr(settings, "MLFLOW_DEV_WORKSPACE", None)
        monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda name: None)
        with pytest.raises(RuntimeError, match="does not exist in this workspace"):
            setup_mlflow("dev")


# =========================================================================
# 2. Case Execution & Resilience Unit Tests
# =========================================================================
@pytest.mark.unit
class TestRunnerOneCase:
    """Unit tests for individual case evaluation, error handling, and metric measurements."""

    @pytest.mark.asyncio
    async def test_measure_one_metric_success(self):
        """Verify successful metric measurement extracts score, pass status, and reason."""
        mock_metric = MagicMock()
        mock_metric.a_measure = AsyncMock()
        mock_metric.score = 0.85
        mock_metric.reason = "Good grounding in context."
        mock_metric.is_successful.return_value = True

        tc = LLMTestCase(
            input="query",
            actual_output="answer",
            retrieval_context=["context"],
        )

        res = await _measure_one_metric("faithfulness", mock_metric, tc)
        assert res["faithfulness_score"] == 0.85
        assert res["faithfulness_pass"] is True
        assert "Good grounding" in res["faithfulness_reason"]

    @pytest.mark.asyncio
    async def test_measure_one_metric_exception_handling(self):
        """Verify metric exceptions are caught gracefully returning None score and error reason."""
        mock_metric = MagicMock()
        mock_metric.a_measure = AsyncMock(side_effect=RuntimeError("LLM API Timeout"))

        tc = LLMTestCase(input="query", actual_output="answer", retrieval_context=["context"])
        res = await _measure_one_metric("faithfulness", mock_metric, tc)

        assert res["faithfulness_score"] is None
        assert res["faithfulness_pass"] is None
        assert "LLM API Timeout" in res["faithfulness_reason"]

    @pytest.mark.asyncio
    async def test_run_one_case_empty_response_fallback(self):
        """Verify pipeline returning empty response receives fallback answer and does not crash."""
        mock_workflow = MagicMock()
        mock_workflow.run = AsyncMock(return_value=[])

        qa = {"question": "Was regelt § 315c?", "answer": "Erwartete Antwort"}
        res = await _run_one_case(
            qa=qa,
            i=0,
            workflow=mock_workflow,
            metric_specs={},
            metric_kwargs={},
            num_qa_pairs=1,
        )

        assert res["index"] == 0
        assert res["error"] is None
        assert "Error: The model failed to generate any output." in res["answer"]
        assert res["deflection"] is False

    @pytest.mark.asyncio
    async def test_run_one_case_deflection_flag(self):
        """Verify deflection phrases in model response set deflection=True in output row."""
        from deepfix_rag.pipeline import RetrievalResult

        mock_workflow = MagicMock()
        mock_workflow.run = AsyncMock(
            return_value=[
                RetrievalResult(
                    doc_id="doc_1",
                    score=1.0,
                    text="I do not have enough information in the provided context.",
                )
            ]
        )

        qa = {"question": "Frage?", "answer": "Antwort"}
        res = await _run_one_case(
            qa=qa,
            i=0,
            workflow=mock_workflow,
            metric_specs={},
            metric_kwargs={},
            num_qa_pairs=1,
        )

        assert res["deflection"] is True

    @pytest.mark.asyncio
    async def test_run_one_case_workflow_exception(self):
        """Verify workflow execution failure is captured in row error field."""
        mock_workflow = MagicMock()
        mock_workflow.run = AsyncMock(side_effect=ConnectionError("LanceDB connection lost"))

        qa = {"question": "Frage?", "answer": "Antwort"}
        res = await _run_one_case(
            qa=qa,
            i=0,
            workflow=mock_workflow,
            metric_specs={},
            metric_kwargs={},
            num_qa_pairs=1,
        )

        assert res["index"] == 0
        assert "LanceDB connection lost" in res["error"]
        assert res["answer"] is None


# =========================================================================
# 3. Live End-to-End Evaluation Tests
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
        isolated_mlflow_env: str,
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
        assert "faithfulness_avg" in results
        assert isinstance(results["faithfulness_avg"], float)
        assert len(results["rows"]) == 3

        first_row = results["rows"][0]
        assert first_row["question"] != ""
        assert first_row["answer"] is not None
        assert first_row["faithfulness_score"] is not None

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

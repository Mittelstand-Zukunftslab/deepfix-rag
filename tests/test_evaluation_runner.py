"""Tests for Evaluation Runner, Deflection Detection, Metric Computation, and Live Pipeline Scoring."""

from pathlib import Path

import mlflow
import pandas as pd
from pyterrier.measures import R, nDCG
import pytest

from deepfix_rag.config.schema import DataLoaderConfig, GenerationMetricType, RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.evaluation.runner import (
    DEFLECTION_PHRASES,
    CaseResult,
    EvaluationRunner,
    EvaluationSummary,
    EvaluationTracker,
    RetrievalEvaluator,
    _resolve_retrieval_measures,
    compute_retrieval_metrics,
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

        monkeypatch.setattr(settings, "MLFLOW_DEV_WORKSPACE", None)
        monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda name: None)
        with pytest.raises(RuntimeError, match="does not exist in this workspace"):
            setup_mlflow("dev")


# =========================================================================
# 2. Retrieval Evaluation & PyTerrier Unit Tests
# =========================================================================
@pytest.mark.unit
class TestRetrievalEvaluation:
    """Unit tests for PyTerrier retrieval measure resolution and metric computation."""

    def test_resolve_retrieval_measures(self):
        """Verify resolution of string/enum retrieval metrics to PyTerrier measure objects."""
        measures = _resolve_retrieval_measures(
            ["ndcg", "recall", "mrr", "map", "unknown_metric"], cutoff=5
        )
        measure_strs = [str(m) for m in measures]
        assert "nDCG@5" in measure_strs
        assert "R@5" in measure_strs
        assert "RR" in measure_strs
        assert "AP" in measure_strs
        assert len(measures) == 4

    def test_compute_retrieval_metrics(self):
        """Verify compute_retrieval_metrics calculates valid NDCG and Recall scores."""

        qrels = pd.DataFrame(
            [
                {"qid": "q1", "docno": "d1", "label": 1},
                {"qid": "q2", "docno": "d2", "label": 1},
            ]
        )
        res = pd.DataFrame(
            [
                {"qid": "q1", "docno": "d1", "score": 1.0, "rank": 0},
                {"qid": "q1", "docno": "d3", "score": 0.5, "rank": 1},
                {"qid": "q2", "docno": "d4", "score": 1.0, "rank": 0},
                {"qid": "q2", "docno": "d2", "score": 0.5, "rank": 1},
            ]
        )

        per_query, agg = compute_retrieval_metrics(res, qrels, [nDCG @ 2, R @ 2])
        assert "q1" in per_query
        assert "q2" in per_query
        assert per_query["q1"]["nDCG@2"] == 1.0
        assert per_query["q1"]["R@2"] == 1.0
        assert per_query["q2"]["R@2"] == 1.0
        assert "nDCG@2" in agg
        assert "R@2" in agg

    def test_compute_retrieval_metrics_empty(self):
        """Verify compute_retrieval_metrics handles empty inputs gracefully."""

        per_query, agg = compute_retrieval_metrics(pd.DataFrame(), pd.DataFrame(), [nDCG @ 5])
        assert per_query == {}
        assert agg == {}


# =========================================================================
# 3. Object-Oriented Evaluation Architecture Tests
# =========================================================================
@pytest.mark.unit
class TestObjectOrientedEvaluators:
    """Unit tests verifying the object-oriented evaluation architecture, models, and tracker."""

    def test_case_result_to_dict_flattening(self):
        """Verify CaseResult flattens metric dictionary entries into the root dictionary."""
        case = CaseResult(
            index=0,
            query_id="q_01",
            question="What is the law?",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1", "doc_2"],
            retrieved_scores=[0.95, 0.45],
            answer="The law is...",
            deflection=False,
            metrics={"faithfulness_score": 0.85, "ndcg_score": 1.0},
        )
        data = case.to_dict()
        assert data["index"] == 0
        assert data["query_id"] == "q_01"
        assert data["ground_truth_doc_ids"] == ["doc_1"]
        assert data["retrieved_doc_ids"] == ["doc_1", "doc_2"]
        assert data["faithfulness_score"] == 0.85
        assert data["ndcg_score"] == 1.0

    def test_evaluation_summary_dict_compatibility(self):
        """Verify EvaluationSummary implements dict-like access for seamless backwards compatibility."""
        summary = EvaluationSummary(
            total=5,
            scored=5,
            errors=0,
            deflection_rate=0.2,
            metrics={"faithfulness_avg": 0.9, "ndcg_avg": 0.85},
            rows=[{"query_id": "q1"}],
        )
        assert summary["total"] == 5
        assert summary["faithfulness_avg"] == 0.9
        assert summary["ndcg_avg"] == 0.85
        assert summary.get("errors") == 0
        assert summary.get("nonexistent", "fallback") == "fallback"
        assert "deflection_rate" in summary
        assert "rows" in summary
        assert set(summary.keys()) >= {"total", "scored", "errors", "deflection_rate", "rows"}
        assert len(summary.values()) > 0
        assert ("faithfulness_avg", 0.9) in summary.items()

    def test_retrieval_evaluator_evaluate_and_populate(self):
        """Verify RetrievalEvaluator evaluates dataframes and populates CaseResult metrics."""
        qrels = pd.DataFrame([{"qid": "q1", "docno": "d1", "label": 1}])
        res = pd.DataFrame(
            [
                {"qid": "q1", "docno": "d1", "score": 1.0, "rank": 0},
                {"qid": "q1", "docno": "d2", "score": 0.5, "rank": 1},
            ]
        )

        evaluator = RetrievalEvaluator(measures=["ndcg", "recall"], cutoff_k=2)
        eval_result = evaluator.evaluate(res, qrels)

        assert "nDCG@2" in eval_result.aggregated
        assert "R@2" in eval_result.aggregated
        assert eval_result.aggregated["nDCG@2"] == 1.0
        assert eval_result.aggregated["R@2"] == 1.0

        case = CaseResult(index=0, query_id="q1", question="Q?")
        evaluator.populate_case_metrics([case], eval_result)
        assert case.metrics["ndcg_score"] == 1.0
        assert case.metrics["recall_score"] == 1.0

    def test_evaluation_tracker_sanitization(self):
        """Verify EvaluationTracker sanitizes '@' to '_at_' for MLflow key compliance."""
        assert EvaluationTracker.sanitize_metric_key("nDCG@10") == "nDCG_at_10"
        assert EvaluationTracker.sanitize_metric_key("R@5") == "R_at_5"
        assert EvaluationTracker.sanitize_metric_key("faithfulness_avg") == "faithfulness_avg"

    def test_evaluation_runner_init_defaults(self):
        """Verify EvaluationRunner configures appropriate sub-evaluators and cutoff from RAGConfig."""
        config = RAGConfig()
        config.retrieval.top_k = 7
        config.retrieval_only = True

        runner = EvaluationRunner(config=config)
        assert runner.cutoff_k == 7
        assert runner.retrieval_evaluator is not None
        assert runner.retrieval_evaluator.cutoff_k == 7
        assert runner.generation_evaluator is None
        assert runner.tracker is not None


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

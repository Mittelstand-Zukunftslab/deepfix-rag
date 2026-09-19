"""Tests for Evaluation Runner, Deflection Detection, Metric Computation, and Live Pipeline Scoring."""

from pathlib import Path

import pandas as pd
from pyterrier.measures import P, R, nDCG
import pytest

from deepfix_rag.config.schema import (
    DataLoaderConfig,
    GenerationMetricType,
    RAGConfig,
    RetrievalMetricType,
)
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.evaluation.runner import (
    DEFLECTION_PHRASES,
    CaseResult,
    EvaluationRunner,
    EvaluationSummary,
    EvaluationTracker,
    GenerationEvaluator,
    RAGFailureMode,
    RetrievalEvaluator,
    classify_case_failure,
    count_tokens,
    is_deflection,
    run_evaluation,
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

    def test_generation_evaluator_metric_classes(self):
        """Verify GenerationEvaluator registers configured DeepEval metric classes."""
        judge = ProxyJudgeLLM(
            model="test-judge",
            api_base="https://example.com/v1",
            api_key="test-key",
        )
        evaluator = GenerationEvaluator(
            generation_metrics=[
                GenerationMetricType.FAITHFULNESS,
                GenerationMetricType.ANSWER_RELEVANCY,
            ],
            threshold=0.7,
            judge=judge,
        )
        assert GenerationMetricType.FAITHFULNESS in evaluator._metric_classes
        assert GenerationMetricType.ANSWER_RELEVANCY in evaluator._metric_classes
        assert evaluator.threshold == 0.7
        assert "test-judge" in evaluator.judge.model


# =========================================================================
# 2. Retrieval Evaluation & PyTerrier Unit Tests
# =========================================================================
@pytest.mark.unit
class TestRetrievalEvaluation:
    """Unit tests for PyTerrier retrieval measure resolution and metric computation."""

    def test_resolve_retrieval_measures(self):
        """Verify resolution of string/enum retrieval metrics to PyTerrier measure objects."""
        measures = RetrievalEvaluator._resolve_measures(
            [
                "ndcg",
                "recall",
                "precision",
                "p",
                RetrievalMetricType.PRECISION,
                "mrr",
                "map",
                "unknown_metric",
            ],
            cutoff=5,
        )
        measure_strs = [str(m) for m in measures]
        assert "nDCG@5" in measure_strs
        assert "R@5" in measure_strs
        assert "P@5" in measure_strs
        assert "RR" in measure_strs
        assert "AP" in measure_strs
        assert len(measures) == 7

    def test_compute_retrieval_metrics(self):
        """Verify RetrievalEvaluator calculates valid NDCG, Recall, and Precision scores."""

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

        evaluator = RetrievalEvaluator(cutoff_k=2)
        evaluator.resolved_measures = [nDCG @ 2, R @ 2, P @ 2]
        eval_result = evaluator.evaluate(res, qrels)

        assert "q1" in eval_result.per_query
        assert "q2" in eval_result.per_query
        assert eval_result.per_query["q1"]["nDCG@2"] == 1.0
        assert eval_result.per_query["q1"]["R@2"] == 1.0
        assert eval_result.per_query["q1"]["P@2"] == 0.5
        assert eval_result.per_query["q2"]["R@2"] == 1.0
        assert eval_result.per_query["q2"]["P@2"] == 0.5
        assert "nDCG@2" in eval_result.aggregated
        assert "R@2" in eval_result.aggregated
        assert "P@2" in eval_result.aggregated

    def test_compute_retrieval_metrics_empty(self):
        """Verify RetrievalEvaluator handles empty inputs gracefully."""

        evaluator = RetrievalEvaluator(cutoff_k=5)
        evaluator.resolved_measures = [nDCG @ 5]
        eval_result = evaluator.evaluate(pd.DataFrame(), pd.DataFrame())
        assert eval_result.per_query == {}
        assert eval_result.aggregated == {}


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
            metrics={"faithfulness": 0.85, "ndcg": 1.0},
        )
        data = case.to_dict()
        assert data["index"] == 0
        assert data["query_id"] == "q_01"
        assert data["ground_truth_doc_ids"] == ["doc_1"]
        assert data["retrieved_doc_ids"] == ["doc_1", "doc_2"]
        assert data["faithfulness"] == 0.85
        assert data["ndcg"] == 1.0

    def test_evaluation_summary_dict_compatibility(self):
        """Verify EvaluationSummary implements dict-like access for seamless backwards compatibility."""
        summary = EvaluationSummary(
            total=5,
            scored=5,
            errors=0,
            deflection_rate=0.2,
            metrics={"faithfulness": 0.9, "ndcg": 0.85},
            rows=[{"query_id": "q1"}],
        )
        assert summary["total"] == 5
        assert summary["faithfulness"] == 0.9
        assert summary["ndcg"] == 0.85
        assert summary.get("errors") == 0
        assert summary.get("nonexistent", "fallback") == "fallback"
        assert "deflection_rate" in summary
        assert "rows" in summary
        assert set(summary.keys()) >= {"total", "scored", "errors", "deflection_rate", "rows"}
        assert len(summary.values()) > 0
        assert ("faithfulness", 0.9) in summary.items()

    def test_retrieval_evaluator_evaluate_and_populate(self):
        """Verify RetrievalEvaluator evaluates dataframes and populates CaseResult metrics."""
        qrels = pd.DataFrame([{"qid": "q1", "docno": "d1", "label": 1}])
        res = pd.DataFrame(
            [
                {"qid": "q1", "docno": "d1", "score": 1.0, "rank": 0},
                {"qid": "q1", "docno": "d2", "score": 0.5, "rank": 1},
            ]
        )

        evaluator = RetrievalEvaluator(measures=["ndcg", "recall", "precision"], cutoff_k=2)
        eval_result = evaluator.evaluate(res, qrels)

        assert "nDCG@2" in eval_result.aggregated
        assert "R@2" in eval_result.aggregated
        assert "P@2" in eval_result.aggregated
        assert eval_result.aggregated["nDCG@2"] == 1.0
        assert eval_result.aggregated["R@2"] == 1.0
        assert eval_result.aggregated["P@2"] == 0.5

        case = CaseResult(index=0, query_id="q1", question="Q?")
        evaluator.populate_case_metrics([case], eval_result)
        assert case.metrics["ndcg"] == 1.0
        assert case.metrics["recall"] == 1.0
        assert case.metrics["precision"] == 0.5

    def test_evaluation_tracker_sanitization(self):
        """Verify EvaluationTracker sanitizes '@' to '_at_' for MLflow key compliance."""
        assert EvaluationTracker.sanitize_metric_key("nDCG@10") == "nDCG_at_10"
        assert EvaluationTracker.sanitize_metric_key("R@5") == "R_at_5"
        assert EvaluationTracker.sanitize_metric_key("P@10") == "P_at_10"
        assert EvaluationTracker.sanitize_metric_key("faithfulness") == "faithfulness"

    def test_evaluation_runner_init_defaults(self):
        """Verify EvaluationRunner configures appropriate sub-evaluators and cutoff from RAGConfig."""
        config = RAGConfig()
        config.eval.cutoff_k = 7
        config.retrieval.top_k = 7
        config.retrieval_only = True

        runner = EvaluationRunner(config=config)
        assert runner.cutoff_k == 7
        assert runner.retrieval_evaluator is not None
        assert runner.retrieval_evaluator.cutoff_k == 7
        assert runner.generation_evaluator is None
        assert runner.tracker is not None

    def test_classify_case_failure_scenarios(self):
        """Verify deterministic decomposition of all RAG failure modes."""
        # 1. Pure retrieval miss: R@k = 0.0
        case_miss = CaseResult(
            index=0,
            query_id="q1",
            question="What is the statute?",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_99"],
            metrics={"recall": 0.0, "precision": 0.0},
        )
        assert classify_case_failure(case_miss) == RAGFailureMode.RETRIEVAL_MISS

        # 2. Retrieval-induced reasoning error: 0 < R@k < 1.0 with generation failure
        case_partial = CaseResult(
            index=1,
            query_id="q2",
            question="Analyze section 1 and 2.",
            ground_truth_doc_ids=["doc_1", "doc_2"],
            retrieved_doc_ids=["doc_1"],
            metrics={
                "recall": 0.5,
                "precision": 1.0,
                "faithfulness": 0.4,
            },
        )
        assert (
            classify_case_failure(case_partial, threshold=0.7)
            == RAGFailureMode.RETRIEVAL_INDUCED_ERROR
        )

        # 3. Retrieval-induced reasoning error: R@k = 1.0 but P@k < 0.5 (distractor noise) with generation failure
        case_noisy = CaseResult(
            index=2,
            query_id="q3",
            question="What is the penalty?",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1", "noise_1", "noise_2", "noise_3"],
            metrics={
                "recall": 1.0,
                "precision": 0.25,
                "faithfulness": 0.5,
            },
        )
        assert (
            classify_case_failure(case_noisy, threshold=0.7)
            == RAGFailureMode.RETRIEVAL_INDUCED_ERROR
        )

        # 4. LLM reasoning failure: R@k = 1.0, clean context (P@k >= 0.5), but low faithfulness
        case_reasoning_fail = CaseResult(
            index=3,
            query_id="q4",
            question="Interpret paragraph 3.",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1"],
            metrics={
                "recall": 1.0,
                "precision": 1.0,
                "faithfulness": 0.45,
            },
        )
        assert (
            classify_case_failure(case_reasoning_fail, threshold=0.7)
            == RAGFailureMode.LLM_REASONING_FAILURE
        )

        # 5. Success case: R@k = 1.0 with high faithfulness
        case_success = CaseResult(
            index=4,
            query_id="q5",
            question="What is the definition?",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1"],
            metrics={
                "recall": 1.0,
                "precision": 1.0,
                "faithfulness": 0.95,
            },
        )
        assert classify_case_failure(case_success, threshold=0.7) == RAGFailureMode.SUCCESS

        # 6. Deflection failure: answerable query with full retrieval, but model deflected
        case_deflect_fail = CaseResult(
            index=5,
            query_id="q6",
            question="Explain the statute.",
            ground_truth_doc_ids=["doc_1"],
            retrieved_doc_ids=["doc_1"],
            deflection=True,
            metrics={"recall": 1.0, "precision": 1.0},
        )
        assert classify_case_failure(case_deflect_fail) == RAGFailureMode.DEFLECTION_FAILURE

        # 7. Appropriate deflection on unanswerable query
        case_unanswerable_ok = CaseResult(
            index=6,
            query_id="q7",
            question="What is Martian law?",
            ground_truth_doc_ids=[],
            deflection=True,
        )
        assert classify_case_failure(case_unanswerable_ok) == RAGFailureMode.SUCCESS

        # 8. Deflection failure on unanswerable query (model hallucinated answer instead of deflecting)
        case_unanswerable_hallucinated = CaseResult(
            index=7,
            query_id="q8",
            question="What is Martian law?",
            ground_truth_doc_ids=[],
            deflection=False,
            answer="Martian law requires helmets.",
        )
        assert (
            classify_case_failure(case_unanswerable_hallucinated)
            == RAGFailureMode.DEFLECTION_FAILURE
        )

    def test_evaluation_summary_error_decomposition(self):
        """Verify EvaluationSummary records and exposes error decomposition fields."""
        summary = EvaluationSummary(
            total=10,
            scored=10,
            errors=0,
            deflection_rate=0.1,
            retrieval_induced_error_count=3,
            retrieval_induced_error_rate=0.3,
            failure_distribution={
                "success": 5,
                "retrieval_miss": 1,
                "retrieval_induced_error": 3,
                "llm_reasoning_failure": 1,
                "deflection_failure": 0,
            },
            metrics={"ndcg": 0.8},
            rows=[{"query_id": "q1", "failure_mode": "retrieval_induced_error"}],
        )

        assert summary.retrieval_induced_error_count == 3
        assert summary.retrieval_induced_error_rate == 0.3
        assert summary.failure_distribution["retrieval_induced_error"] == 3

        data = summary.to_dict()
        assert data["retrieval_induced_error_count"] == 3
        assert data["retrieval_induced_error_rate"] == 0.3
        assert data["failure_distribution"]["success"] == 5
        assert summary["retrieval_induced_error_count"] == 3
        assert summary["failure_distribution"]["retrieval_miss"] == 1
        assert "retrieval_induced_error_rate" in summary

    def test_compute_error_reduction(self):
        """Verify calculation of retrieval-induced error reduction percentage and target_met flag."""
        from deepfix_rag.cli.eval import compute_error_reduction

        # 30% reduction: target met (>= 15%)
        pct, met = compute_error_reduction(baseline_err=10, candidate_err=7)
        assert pct == 30.0
        assert met is True

        # 10% reduction: target not met (< 15%)
        pct, met = compute_error_reduction(baseline_err=10, candidate_err=9)
        assert pct == 10.0
        assert met is False

        # 100% reduction: zero errors in candidate
        pct, met = compute_error_reduction(baseline_err=10, candidate_err=0)
        assert pct == 100.0
        assert met is True

        # Negative reduction: candidate introduced more errors
        pct, met = compute_error_reduction(baseline_err=10, candidate_err=12)
        assert pct == -20.0
        assert met is False

        # Zero baseline errors: cannot reduce
        pct, met = compute_error_reduction(baseline_err=0, candidate_err=0)
        assert pct == 0.0
        assert met is False

        # None values handled gracefully
        pct, met = compute_error_reduction(baseline_err=None, candidate_err=5)
        assert pct is None
        assert met is False


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

"""Run DeepEval and PyTerrier scoring over a dataset using the RAG pipeline."""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BaseMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.test_case import LLMTestCase
from llama_index.core.workflow import Context
import mlflow
import pandas as pd
import pyterrier as pt
from pyterrier.measures import AP, RR, R, nDCG
import tiktoken

from deepfix_rag.config.schema import (
    GenerationMetricType,
    RAGConfig,
    RetrievalMetricType,
)
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.data import DataLoader
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.pipeline import RAGWorkflow, RetrievalResult

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text or ""))


DEFLECTION_PHRASES = (
    "i do not have enough information",
    "i don't have enough information",
    "not enough information",
    "i cannot answer",
    "the context does not provide",
    "the provided text does not",
    "not mentioned in the context",
    "i am unable to answer",
    "does not contain the answer to this question",
)


def is_deflection(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(phrase in lowered for phrase in DEFLECTION_PHRASES)


def load_qa_dataset(dataset_path: Path | str, split: str = "test") -> list[dict]:
    return DataLoader().load_qa(dataset_path, split=split)


# =========================================================================
# Data Models
# =========================================================================


@dataclass
class CaseResult:
    """Evaluation result for an individual QA case."""

    index: int
    query_id: str
    question: str
    ground_truth_doc_ids: list[str] = field(default_factory=list)
    retrieved_doc_ids: list[str] = field(default_factory=list)
    retrieved_scores: list[float] = field(default_factory=list)
    answer: str | None = None
    deflection: bool = False
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to flat dictionary matching standard schema for DataFrame & MLflow logging."""
        base = {
            "index": self.index,
            "query_id": self.query_id,
            "question": self.question,
            "ground_truth_doc_ids": self.ground_truth_doc_ids,
            "retrieved_doc_ids": self.retrieved_doc_ids,
            "retrieved_scores": self.retrieved_scores,
            "answer": self.answer,
            "deflection": self.deflection,
            "error": self.error,
        }
        base.update(self.metrics)
        return base


@dataclass
class RetrievalEvalResult:
    """Results from PyTerrier evaluation across all queries."""

    per_query: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    aggregated: dict[str, float] = field(default_factory=dict)


@dataclass
class EvaluationSummary:
    """Aggregated evaluation results with dictionary compatibility for seamless caller integration."""

    total: int
    scored: int
    errors: int
    deflection_rate: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    error_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "total": self.total,
            "scored": self.scored,
            "errors": self.errors,
            "deflection_rate": self.deflection_rate,
        }
        res.update(self.metrics)
        res["rows"] = self.rows
        res["error_details"] = self.error_details
        return res

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()

    def items(self) -> Any:
        return self.to_dict().items()

    def keys(self) -> Any:
        return self.to_dict().keys()

    def values(self) -> Any:
        return self.to_dict().values()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())


# =========================================================================
# Specialized Evaluator Classes
# =========================================================================


class RetrievalEvaluator:
    """Evaluates retrieval performance using PyTerrier measures."""

    def __init__(
        self,
        measures: list[str | RetrievalMetricType] | None = None,
        cutoff_k: int = 10,
    ) -> None:
        self.cutoff_k = cutoff_k
        self.metric_names = measures or [RetrievalMetricType.NDCG, RetrievalMetricType.RECALL]
        self.resolved_measures = self._resolve_measures(self.metric_names, self.cutoff_k)

    @staticmethod
    def _resolve_measures(
        metric_names: list[RetrievalMetricType | str],
        cutoff: int,
    ) -> list[Any]:
        measures = []
        for m in metric_names:
            m_lower = str(m).lower()
            if m_lower in ("ndcg", "ndcg_at_k"):
                measures.append(nDCG @ cutoff)
            elif m_lower in ("recall", "recall_at_k", "r"):
                measures.append(R @ cutoff)
            elif m_lower in ("mrr", "rr"):
                measures.append(RR)
            elif m_lower in ("map", "ap"):
                measures.append(AP)
            else:
                LOGGER.warning(f"Unknown retrieval metric '{m}', skipping.")
        return measures

    def evaluate(
        self,
        res_df: pd.DataFrame,
        qrels_df: pd.DataFrame,
    ) -> RetrievalEvalResult:
        """Run PyTerrier evaluation and return per-query and aggregated metrics."""
        if res_df.empty or qrels_df.empty or not self.resolved_measures:
            return RetrievalEvalResult()

        try:
            per_query_raw = pt.Evaluate(
                res_df, qrels_df, metrics=self.resolved_measures, perquery=True
            )
            agg_raw = pt.Evaluate(res_df, qrels_df, metrics=self.resolved_measures, perquery=False)
            return RetrievalEvalResult(per_query=per_query_raw, aggregated=agg_raw)
        except Exception as e:
            LOGGER.error(f"PyTerrier evaluation failed: {e}")
            return RetrievalEvalResult()

    def populate_case_metrics(
        self,
        cases: list[CaseResult],
        eval_result: RetrievalEvalResult,
    ) -> None:
        """Enrich CaseResult instances with retrieval scores."""
        ndcg_key = str(nDCG @ self.cutoff_k)
        recall_key = str(R @ self.cutoff_k)

        for case in cases:
            q_scores = eval_result.per_query.get(case.query_id, {})
            case.metrics["ndcg_score"] = q_scores.get(ndcg_key)
            case.metrics["recall_score"] = q_scores.get(recall_key)
            for m in self.resolved_measures:
                case.metrics[str(m)] = q_scores.get(str(m))


class GenerationEvaluator:
    """Evaluates generation quality using DeepEval metrics and LLM judge."""

    def __init__(
        self,
        generation_metrics: list[GenerationMetricType] | None = None,
        threshold: float = 0.5,
        judge: ProxyJudgeLLM | None = None,
    ) -> None:
        self.threshold = threshold
        self.judge = judge or ProxyJudgeLLM(
            model=settings.LLM_JUDGE_MODEL,
            api_base=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        self.generation_metrics = generation_metrics or []
        self._metric_classes = self._build_metric_classes(self.generation_metrics)

    @staticmethod
    def _build_metric_classes(
        metrics: list[GenerationMetricType],
    ) -> dict[str, type[BaseMetric]]:
        metrics_map: dict[GenerationMetricType, type[BaseMetric]] = {
            GenerationMetricType.FAITHFULNESS: FaithfulnessMetric,
            GenerationMetricType.CONTEXT_PRECISION: ContextualPrecisionMetric,
            GenerationMetricType.CONTEXTUAL_RELEVANCY: ContextualRelevancyMetric,
            GenerationMetricType.CONTEXT_RECALL: ContextualRecallMetric,
            GenerationMetricType.ANSWER_RELEVANCY: AnswerRelevancyMetric,
        }
        return {mt: metrics_map[mt] for mt in metrics if mt in metrics_map}

    async def _measure_one(
        self, metric_type: str, metric: BaseMetric, tc: LLMTestCase
    ) -> dict[str, Any]:
        try:
            if getattr(metric, "async_mode", False) and hasattr(metric, "a_measure"):
                await metric.a_measure(tc)
            elif hasattr(metric, "measure"):
                await asyncio.to_thread(metric.measure, tc)
            elif hasattr(metric, "a_measure"):
                await metric.a_measure(tc)

            score = getattr(metric, "score", 0.0)
            reason = getattr(metric, "reason", "No reason provided")
            success = (
                metric.is_successful()
                if hasattr(metric, "is_successful")
                else bool(score >= getattr(metric, "threshold", 0.5))
            )
            return {
                f"{metric_type}_score": score,
                f"{metric_type}_pass": success,
                f"{metric_type}_reason": reason,
            }
        except Exception as e:
            LOGGER.warning(f"Metric {metric_type} failed: {e}")
            return {
                f"{metric_type}_score": None,
                f"{metric_type}_pass": None,
                f"{metric_type}_reason": str(e),
            }

    async def evaluate_case(
        self,
        query: str,
        answer: str,
        expected_output: str,
        context: list[str],
    ) -> dict[str, Any]:
        """Evaluate a single test case across all configured generation metrics concurrently."""
        if not self._metric_classes:
            return {}

        tc = LLMTestCase(
            input=query,
            actual_output=answer,
            expected_output=expected_output or "N/A",
            retrieval_context=context or ["No context provided."],
        )

        metric_kwargs = dict(
            async_mode=False,
            include_reason=True,
            threshold=self.threshold,
            model=self.judge,
        )
        fresh_metrics = {mt: cls(**metric_kwargs) for mt, cls in self._metric_classes.items()}

        results = await asyncio.gather(
            *(self._measure_one(mt, m, tc) for mt, m in fresh_metrics.items())
        )
        merged: dict[str, Any] = {}
        for r in results:
            merged.update(r)
        return merged


class EvaluationTracker:
    """Manages MLflow experiment tracking, metric logging, and table artifacts."""

    def __init__(
        self,
        workspace: str = "dev",
        experiment_name: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.experiment_name = experiment_name or settings.MLFLOW_DEV_RAG_EXPERIMENT

    def setup(self) -> None:
        """Setup MLflow workspace and verify experiment exists."""
        workspace_env = (
            "MLFLOW_TEST_WORKSPACE" if self.workspace == "test" else "MLFLOW_DEV_WORKSPACE"
        )
        if hasattr(mlflow, "set_workspace") and getattr(settings, workspace_env, None):
            mlflow.set_workspace(getattr(settings, workspace_env))

        experiment = mlflow.get_experiment_by_name(self.experiment_name)
        if experiment is None:
            raise RuntimeError(
                f"MLflow experiment '{self.experiment_name}' does not exist in this workspace. "
                f"Refusing to auto-create per contract - check {self.experiment_name}."
            )
        mlflow.set_experiment(experiment_id=experiment.experiment_id)

    @staticmethod
    def sanitize_metric_key(key: str) -> str:
        """Sanitize metric key for MLflow compliance (Names may only contain alphanumerics, _, -, .,  , /)."""
        return str(key).replace("@", "_at_")

    def log_run(
        self,
        run_name: str,
        params: dict[str, Any],
        summary: EvaluationSummary,
    ) -> None:
        """Log parameters, sanitized metrics, and table artifacts inside an MLflow run."""
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(params, synchronous=False)

            flat_summary = summary.to_dict()
            mlflow_metrics = {
                self.sanitize_metric_key(k): float(v)
                for k, v in flat_summary.items()
                if isinstance(v, (int, float)) and v is not None
            }
            mlflow.log_metrics(mlflow_metrics, synchronous=False)

            try:
                df = pd.DataFrame.from_records(summary.rows, index="index")
                for col in ["ground_truth_doc_ids", "retrieved_doc_ids", "retrieved_scores"]:
                    if col in df.columns:
                        df[col] = df[col].apply(lambda x: str(x) if isinstance(x, list) else x)
                mlflow.log_table(data=df, artifact_file="eval_results.json")
            except Exception as e:
                LOGGER.error(f"Failed to log eval results as table: {e}")


# =========================================================================
# Coordinator
# =========================================================================


class EvaluationRunner:
    """Coordinator orchestrating dataset loading, pipeline execution, retrieval/generation evaluation, and tracking."""

    def __init__(
        self,
        config: RAGConfig,
        retrieval_evaluator: RetrievalEvaluator | None = None,
        generation_evaluator: GenerationEvaluator | None = None,
        tracker: EvaluationTracker | None = None,
    ) -> None:
        self.config = config
        self.cutoff_k = (
            config.rerank.top_n
            if (config.rerank and config.rerank.enabled and config.rerank.top_n)
            else (config.retrieval.top_k if (config.retrieval and config.retrieval.top_k) else 10)
        )

        self.retrieval_evaluator = retrieval_evaluator or RetrievalEvaluator(
            measures=config.eval.retrieval_metrics if config.eval else None,
            cutoff_k=self.cutoff_k,
        )

        if not config.retrieval_only:
            self.generation_evaluator = generation_evaluator or GenerationEvaluator(
                generation_metrics=config.eval.generation_metrics if config.eval else None,
                threshold=config.eval.threshold if config.eval else 0.5,
            )
        else:
            self.generation_evaluator = None

        self.tracker = tracker or EvaluationTracker()

    async def _execute_case(
        self,
        qa: dict[str, Any],
        i: int,
        workflow: RAGWorkflow,
    ) -> CaseResult:
        query_id = str(qa.get("query_id") or qa.get("id") or f"q_{i}")
        query = qa.get("question") or qa.get("query", "")
        expected_output = qa.get("answer") or qa.get("expected_output", "N/A")

        gt_doc_ids: list[str] = []
        if "relevant_doc_ids" in qa and isinstance(qa["relevant_doc_ids"], list):
            gt_doc_ids.extend(str(d) for d in qa["relevant_doc_ids"] if d)
        elif "doc_ids" in qa and isinstance(qa["doc_ids"], list):
            gt_doc_ids.extend(str(d) for d in qa["doc_ids"] if d)
        elif "doc_id" in qa and qa["doc_id"]:
            gt_doc_ids.append(str(qa["doc_id"]))

        deflection = False
        retrieved_results: list[RetrievalResult] = []
        answer = ""
        error = None

        ctx = Context(workflow)
        try:
            results: list[RetrievalResult] | None = await workflow.run(ctx=ctx, query=query)
            retrieved_results = results or []
            context = [r.text for r in retrieved_results]

            if not self.config.retrieval_only:
                try:
                    stored_ans = await ctx.store.get("synthesized_response", default=None)
                except Exception:
                    stored_ans = None
                if stored_ans:
                    answer = stored_ans
                else:
                    answer = context[0] if context else ""
        except Exception as e:
            error = str(e)
            LOGGER.warning(f"Case {i} execution error: {e}")
            return CaseResult(
                index=i,
                query_id=query_id,
                question=query,
                ground_truth_doc_ids=gt_doc_ids,
                error=error,
            )

        case = CaseResult(
            index=i,
            query_id=query_id,
            question=query,
            ground_truth_doc_ids=gt_doc_ids,
            retrieved_doc_ids=[r.doc_id for r in retrieved_results],
            retrieved_scores=[r.score for r in retrieved_results],
            error=error,
        )

        if not self.config.retrieval_only:
            if not answer or answer.strip() == "":
                answer = "Error: The model failed to generate any output."

            if is_deflection(answer):
                deflection = True

            case.answer = answer
            case.deflection = deflection

            if self.generation_evaluator:
                context = [r.text for r in retrieved_results]
                gen_metrics = await self.generation_evaluator.evaluate_case(
                    query=query,
                    answer=answer,
                    expected_output=expected_output,
                    context=context,
                )
                case.metrics.update(gen_metrics)

        return case

    async def run(
        self,
        dataset_path: Path | str,
        workspace: str = "dev",
        run_name: str = "eval_run",
        split: str | None = None,
        limit: int | None = None,
        index_dir: Path | str | None = None,
        max_concurrency: int | None = None,
        retrieval_only: bool | None = None,
    ) -> EvaluationSummary:
        overrides: dict[str, Any] = {}
        if split is not None:
            overrides["data.split"] = split
        if limit is not None:
            overrides["data.limit"] = limit
        if index_dir is not None:
            overrides["retrieval.lancedb_index_dir"] = str(index_dir)
        if max_concurrency is not None:
            overrides["eval.concurrency"] = max_concurrency
        if retrieval_only is not None:
            overrides["retrieval_only"] = retrieval_only

        if overrides:
            from deepfix_rag.config.mutator import apply_mutation

            self.config = apply_mutation(self.config, overrides)

        self.tracker.workspace = workspace
        self.tracker.setup()

        qa_pairs = load_qa_dataset(dataset_path, split=self.config.data.split)
        if self.config.data.limit:
            qa_pairs = qa_pairs[: self.config.data.limit]

        qrels_df = DataLoader.to_qrels_df(qa_pairs)

        concurrency_limit = self.config.eval.concurrency or 1
        semaphore = asyncio.Semaphore(concurrency_limit)
        LOGGER.info(
            f"Running evaluation with concurrency={concurrency_limit}, retrieval_only={self.config.retrieval_only}"
        )

        workflow = RAGWorkflow(config=self.config)
        await workflow.run(ingest=True)

        async def _run(qa: dict, i: int) -> CaseResult:
            async with semaphore:
                return await self._execute_case(qa=qa, i=i, workflow=workflow)

        cases = await asyncio.gather(*(_run(qa, i) for i, qa in enumerate(qa_pairs)))

        all_res_rows = []
        for case in cases:
            for rank, (doc_id, score) in enumerate(
                zip(case.retrieved_doc_ids, case.retrieved_scores)
            ):
                all_res_rows.append(
                    {
                        "qid": str(case.query_id),
                        "docno": str(doc_id),
                        "score": float(score),
                        "rank": rank,
                    }
                )

        if all_res_rows:
            res_df = pd.DataFrame(all_res_rows)
            res_df["qid"] = res_df["qid"].astype(str)
            res_df["docno"] = res_df["docno"].astype(str)
            res_df["score"] = res_df["score"].astype(float)
            res_df["rank"] = res_df["rank"].astype(int)
        else:
            res_df = pd.DataFrame(columns=["qid", "docno", "score", "rank"]).astype(
                {"qid": "str", "docno": "str", "score": "float64", "rank": "int64"}
            )

        retrieval_eval_result = self.retrieval_evaluator.evaluate(res_df, qrels_df)
        self.retrieval_evaluator.populate_case_metrics(cases, retrieval_eval_result)

        rows = [c.to_dict() for c in cases]
        errors = [r for r in rows if r.get("error") is not None]

        def avg(key: str) -> float:
            vals = [r[key] for r in rows if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        summary_metrics: dict[str, float] = {}

        if not self.config.retrieval_only and self.generation_evaluator:
            for metric_type in self.generation_evaluator._metric_classes:
                summary_metrics[f"{metric_type}_avg"] = avg(f"{metric_type}_score")

        for m in self.retrieval_evaluator.resolved_measures:
            m_str = str(m)
            m_val = retrieval_eval_result.aggregated.get(m_str, 0.0)
            summary_metrics[m_str] = m_val
            if "ndcg" in m_str.lower():
                summary_metrics["ndcg_avg"] = m_val
            elif "r@" in m_str.lower() or "recall" in m_str.lower():
                summary_metrics["recall_avg"] = m_val

        deflection_rate = (
            sum(r.get("deflection", False) for r in rows) / len(rows)
            if (rows and not self.config.retrieval_only)
            else 0.0
        )

        summary = EvaluationSummary(
            total=len(qa_pairs),
            scored=len(rows),
            errors=len(errors),
            deflection_rate=deflection_rate,
            metrics=summary_metrics,
            rows=rows,
            error_details=errors,
        )

        run_params = {
            **self.config.model_dump(),
            "n_questions": len(qa_pairs),
            "concurrency": concurrency_limit,
            "retrieval_only": self.config.retrieval_only,
            "cutoff_k": self.cutoff_k,
        }
        self.tracker.log_run(run_name=run_name, params=run_params, summary=summary)

        return summary


# =========================================================================
# Module-level Façade & Legacy Compatibility
# =========================================================================


async def run_evaluation(
    dataset_path: Path | str,
    config: RAGConfig,
    workspace: str = "dev",
    run_name: str = "eval_run",
    split: str | None = None,
    limit: int | None = None,
    index_dir: Path | str | None = None,
    max_concurrency: int | None = None,
    retrieval_only: bool | None = None,
) -> EvaluationSummary:
    """Module-level façade orchestrating EvaluationRunner."""
    runner = EvaluationRunner(config=config)
    return await runner.run(
        dataset_path=dataset_path,
        workspace=workspace,
        run_name=run_name,
        split=split,
        limit=limit,
        index_dir=index_dir,
        max_concurrency=max_concurrency,
        retrieval_only=retrieval_only,
    )


def setup_mlflow(workspace: str) -> None:
    """Legacy helper for configuring MLflow."""
    EvaluationTracker(workspace=workspace).setup()


def make_metrics(
    generation_metrics: list[GenerationMetricType],
    async_mode: bool,
    threshold: float,
    judge: ProxyJudgeLLM,
) -> dict[GenerationMetricType, BaseMetric]:
    """Legacy helper for instantiating DeepEval metric instances."""
    kwargs = dict(async_mode=async_mode, include_reason=True, threshold=threshold, model=judge)
    metrics_map = {
        GenerationMetricType.FAITHFULNESS: FaithfulnessMetric,
        GenerationMetricType.CONTEXT_PRECISION: ContextualPrecisionMetric,
        GenerationMetricType.CONTEXTUAL_RELEVANCY: ContextualRelevancyMetric,
        GenerationMetricType.CONTEXT_RECALL: ContextualRecallMetric,
        GenerationMetricType.ANSWER_RELEVANCY: AnswerRelevancyMetric,
    }
    metrics_dict: dict[str, BaseMetric] = {}
    for metric in generation_metrics:
        if metric in metrics_map:
            metrics_dict[metric] = metrics_map[metric](**kwargs)
    return metrics_dict


def _resolve_retrieval_measures(
    metric_names: list[RetrievalMetricType | str],
    cutoff: int,
) -> list[Any]:
    """Legacy helper delegating to RetrievalEvaluator._resolve_measures."""
    return RetrievalEvaluator._resolve_measures(metric_names, cutoff=cutoff)


def compute_retrieval_metrics(
    res_df: pd.DataFrame,
    qrels_df: pd.DataFrame,
    measures: list[Any],
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Legacy helper delegating to RetrievalEvaluator.evaluate."""
    evaluator = RetrievalEvaluator(cutoff_k=10)
    evaluator.resolved_measures = measures
    result = evaluator.evaluate(res_df, qrels_df)
    return result.per_query, result.aggregated

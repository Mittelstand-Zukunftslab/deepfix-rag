"""Run DeepEval scoring over a dataset using the RAG pipeline."""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
from pathlib import Path

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BaseMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.test_case import LLMTestCase
import mlflow
import pandas as pd
import tiktoken

from deepfix_rag.config.schema import GenerationMetricType, RAGConfig
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.data import DataLoader
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.pipeline import RAGWorkflow

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


def setup_mlflow(workspace: str):
    workspace_env = "MLFLOW_TEST_WORKSPACE" if workspace == "test" else "MLFLOW_DEV_WORKSPACE"
    if hasattr(mlflow, "set_workspace") and getattr(settings, workspace_env, None):
        mlflow.set_workspace(getattr(settings, workspace_env))

    experiment = mlflow.get_experiment_by_name(settings.MLFLOW_DEV_RAG_EXPERIMENT)
    if experiment is None:
        raise RuntimeError(
            f"MLflow experiment '{settings.MLFLOW_DEV_RAG_EXPERIMENT}' does not exist in this workspace. "
            "Refusing to auto-create per contract - check MLFLOW_DEV_RAG_EXPERIMENT."
        )
    mlflow.set_experiment(experiment_id=experiment.experiment_id)


def make_metrics(
    generation_metrics: list[GenerationMetricType],
    async_mode: bool,
    threshold: float,
    judge: ProxyJudgeLLM,
) -> dict[GenerationMetricType, BaseMetric]:
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


async def _measure_one_metric(metric_type: str, metric: BaseMetric, tc: LLMTestCase) -> dict:
    """Evaluate a single metric against a test case, returning partial row data."""
    try:
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


async def _run_one_case(
    qa: dict[str, str],
    i: int,
    workflow: RAGWorkflow,
    metric_specs: dict[str, type],
    metric_kwargs: dict,
    num_qa_pairs: int,
) -> dict:
    query = qa.get("question") or qa.get("query", "")
    expected_output = qa.get("answer") or qa.get("expected_output", "N/A")
    # question_id = qa.get("id", f"q_{i}")
    deflection = False
    try:
        LOGGER.info(f"Evaluating Question {i + 1}/{num_qa_pairs}: {query[:50]}...")
        response = await workflow.run(query=query)
        answer = getattr(response, "response", getattr(response, "result", str(response)))
        context = [n.node.get_content() for n in response.source_nodes]

    except Exception as e:
        return {
            "index": i,
            "error": str(e),
            "question": query,
            "answer": None,
            "deflection": deflection,
        }

    # Fallback for LLM Empty Response so eval does not crash the loop
    if not answer or answer.strip() == "":
        answer = "Error: The model failed to generate any output."

    if is_deflection(answer):
        deflection = True

    tc = LLMTestCase(
        input=query,
        actual_output=answer,
        expected_output=expected_output or "N/A",
        retrieval_context=context or ["No context provided."],
    )

    row = {
        "index": i,
        "question": query,
        "answer": answer,
        "error": None,
        "deflection": deflection,
    }

    # Fresh metrics per case to avoid cross-coroutine state pollution
    fresh_metrics = {mt: cls(**metric_kwargs) for mt, cls in metric_specs.items()}

    for metric_type in fresh_metrics:
        row[f"{metric_type}_score"] = None
        row[f"{metric_type}_pass"] = None

    # Run all metrics concurrently for this test case
    metric_results = await asyncio.gather(
        *(_measure_one_metric(mt, m, tc) for mt, m in fresh_metrics.items())
    )
    for partial in metric_results:
        row.update(partial)

    return row


async def run_evaluation(
    dataset_path: Path,
    config: RAGConfig,
    index_dir: str | None = None,
    limit: int | None = None,
    workspace: str = "dev",
    run_name: str = "eval_run",
    synchronous: bool = False,
    max_concurrency: int | None = None,
) -> dict:

    setup_mlflow(workspace)

    qa_pairs = load_qa_dataset(dataset_path)
    if limit:
        qa_pairs = qa_pairs[:limit]

    judge = ProxyJudgeLLM(
        model=settings.LLM_JUDGE_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
    )

    # Build metric class map + shared kwargs (instances are created per-case)
    metrics_map = {
        GenerationMetricType.FAITHFULNESS: FaithfulnessMetric,
        GenerationMetricType.CONTEXT_PRECISION: ContextualPrecisionMetric,
        GenerationMetricType.CONTEXTUAL_RELEVANCY: ContextualRelevancyMetric,
        GenerationMetricType.CONTEXT_RECALL: ContextualRecallMetric,
        GenerationMetricType.ANSWER_RELEVANCY: AnswerRelevancyMetric,
    }
    metric_kwargs = dict(
        async_mode=not synchronous,
        include_reason=True,
        threshold=config.eval.threshold,
        model=judge,
    )
    metric_specs: dict[str, type] = {
        mt: metrics_map[mt] for mt in config.eval.generation_metrics if mt in metrics_map
    }

    # Determine effective concurrency
    concurrency_limit = 1 if synchronous else (max_concurrency or config.eval.concurrency)
    semaphore = asyncio.Semaphore(concurrency_limit)
    LOGGER.info(f"Running evaluation with concurrency={concurrency_limit}")

    workflow = RAGWorkflow(config=config, index_dir=index_dir)
    num_qa_pairs = len(qa_pairs)

    async def _run(qa: dict, i: int) -> dict:
        async with semaphore:
            return await _run_one_case(qa, i, workflow, metric_specs, metric_kwargs, num_qa_pairs)

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(config.model_dump(), synchronous=synchronous)
        mlflow.log_param("n_questions", len(qa_pairs), synchronous=synchronous)
        mlflow.log_param("concurrency", concurrency_limit, synchronous=synchronous)

        # Fan-out all cases concurrently, throttled by semaphore
        all_rows = await asyncio.gather(*(_run(qa, i) for i, qa in enumerate(qa_pairs)))

        rows = list(all_rows)
        errors = [r for r in rows if r["error"] is not None]

        def avg(key: str) -> float:
            vals = [r[key] for r in rows if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        summary = {
            "total": len(qa_pairs),
            "scored": len(rows),
            "errors": len(errors),
            "deflection_rate": sum(r.get("deflection", False) for r in rows) / len(rows)
            if rows
            else 0.0,
        }

        for metric_type in metric_specs:
            summary[f"{metric_type}_avg"] = avg(f"{metric_type}_score")

        mlflow.log_metrics(summary, synchronous=synchronous)
        try:
            df = pd.DataFrame.from_records(rows, index="index")
            mlflow.log_table(data=df, artifact_file="eval_results.json")
        except Exception as e:
            LOGGER.error(f"Failed to log eval results as table: {e}")

    return {**summary, "rows": rows, "error_details": errors}

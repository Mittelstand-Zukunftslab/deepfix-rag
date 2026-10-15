"""Label rules for feedback rows and for metrics that need a reference answer."""

import math
from typing import Any

from deepfix_rag.config.schema import GenerationMetricType

REFERENCE_METRICS = frozenset(
    {
        GenerationMetricType.CONTEXT_PRECISION.value,
        GenerationMetricType.CONTEXT_RECALL.value,
        "answer_correctness",
    }
)

QUESTION_METRIC_KEYS = (
    "ndcg",
    "recall",
    "precision",
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_relevancy",
    "contextual_relevancy",
    "answer_correctness",
)


def needs_reference_answer(metric: GenerationMetricType | str) -> bool:
    """True when the metric cannot be scored without an expected answer."""
    name = str(metric).lower()
    return name in REFERENCE_METRICS or "correctness" in name


def generation_metrics_for_record(
    metrics: list[GenerationMetricType | str],
    answer: str | None,
) -> list[GenerationMetricType | str]:
    """Drop reference-based metrics when the row has no reference answer."""
    if answer is not None:
        return list(metrics)
    return [metric for metric in metrics if not needs_reference_answer(metric)]


def mean_present(rows: list[dict[str, Any]], key: str) -> float | None:
    """Average numeric values for ``key``. A metric with no values is absent."""
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    if not values:
        return None
    return sum(values) / len(values)


def question_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-question metric values, omitting metrics that were not scored."""
    questions: list[dict[str, Any]] = []
    for row in rows:
        metrics = {
            key: row[key] for key in QUESTION_METRIC_KEYS if isinstance(row.get(key), (int, float))
        }
        questions.append({"query_id": row.get("query_id"), "metrics": metrics})
    return questions


def is_rank_metric_key(key: str) -> bool:
    """True for nDCG, recall, and precision keys, not generation metrics."""
    lowered = key.lower()
    if lowered.startswith("context"):
        return False
    return lowered in {"ndcg", "recall", "precision"} or lowered.startswith(
        ("ndcg", "p@", "r@", "precision@", "recall@")
    )


def judged_retrieval_metrics(
    retrieved: list[str],
    relevant: list[str],
    negatives: list[str],
    cutoff: int,
) -> dict[str, float | None]:
    """Binary judged-only precision, nDCG, and recall.

    Documents in neither list are left out of precision and nDCG. Recall uses only
    the relevant ids. An empty relevant list omits recall.
    """
    relevant_ids = list(dict.fromkeys(relevant))
    negative_ids = set(negatives)
    relevant_set = set(relevant_ids)
    window = retrieved[:cutoff]
    judged = [doc_id for doc_id in window if doc_id in relevant_set or doc_id in negative_ids]

    recall: float | None
    if not relevant_ids:
        recall = None
    else:
        found = {doc_id for doc_id in window if doc_id in relevant_set}
        recall = len(found) / len(relevant_ids)

    if not judged:
        return {"precision": None, "ndcg": None, "recall": recall}

    relevant_hits = sum(1 for doc_id in judged if doc_id in relevant_set)
    precision = relevant_hits / len(judged)
    gains = [1.0 if doc_id in relevant_set else 0.0 for doc_id in judged]
    return {"precision": precision, "ndcg": _ndcg(gains), "recall": recall}


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def _ndcg(gains: list[float]) -> float:
    ideal = _dcg(sorted(gains, reverse=True))
    if ideal == 0.0:
        return 0.0
    return _dcg(gains) / ideal

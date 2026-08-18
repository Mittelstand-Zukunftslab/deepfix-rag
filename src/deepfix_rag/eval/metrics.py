"""Metric extraction & scoring aggregators."""

from pydantic import BaseModel, Field


class EvalResult(BaseModel):
    """DeepEval aggregated metric scores."""

    faithfulness: float = Field(default=0.0, ge=0.0, le=1.0)
    context_precision: float = Field(default=0.0, ge=0.0, le=1.0)
    answer_correctness: float = Field(default=0.0, ge=0.0, le=1.0)
    deflection_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    num_samples: int = Field(default=0, ge=0)
    per_query_scores: list[dict[str, float]] = Field(default_factory=list)


def aggregate_scores(scores: list[dict[str, float]]) -> EvalResult:
    """Aggregate per-query scores into mean metric benchmarks."""
    if not scores:
        return EvalResult()

    faithfulness = sum(s.get("faithfulness", 0.0) for s in scores) / len(scores)
    context_precision = sum(s.get("context_precision", 0.0) for s in scores) / len(scores)
    answer_correctness = sum(s.get("answer_correctness", 0.0) for s in scores) / len(scores)
    deflection_rate = sum(s.get("deflection_rate", 0.0) for s in scores) / len(scores)

    return EvalResult(
        faithfulness=faithfulness,
        context_precision=context_precision,
        answer_correctness=answer_correctness,
        deflection_rate=deflection_rate,
        num_samples=len(scores),
        per_query_scores=scores,
    )

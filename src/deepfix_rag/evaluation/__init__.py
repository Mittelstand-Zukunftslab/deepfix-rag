"""Evaluation module for DeepFix RAG."""

from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.evaluation.runner import (
    CaseResult,
    EvaluationRunner,
    EvaluationSummary,
    EvaluationTracker,
    GenerationEvaluator,
    RAGFailureMode,
    RetrievalEvalResult,
    RetrievalEvaluator,
    classify_case_failure,
    count_tokens,
    is_deflection,
    run_evaluation,
)

__all__ = [
    "CaseResult",
    "EvaluationRunner",
    "EvaluationSummary",
    "EvaluationTracker",
    "GenerationEvaluator",
    "ProxyJudgeLLM",
    "RAGFailureMode",
    "RetrievalEvalResult",
    "RetrievalEvaluator",
    "classify_case_failure",
    "count_tokens",
    "is_deflection",
    "run_evaluation",
]

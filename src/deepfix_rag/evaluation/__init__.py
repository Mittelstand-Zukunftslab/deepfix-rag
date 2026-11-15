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
    run_evaluation,
)
from deepfix_rag.grounding import DEFLECTION_PHRASES, Grounding, grounding_for, is_deflection

__all__ = [
    "CaseResult",
    "DEFLECTION_PHRASES",
    "EvaluationRunner",
    "EvaluationSummary",
    "EvaluationTracker",
    "GenerationEvaluator",
    "Grounding",
    "ProxyJudgeLLM",
    "RAGFailureMode",
    "RetrievalEvalResult",
    "RetrievalEvaluator",
    "classify_case_failure",
    "count_tokens",
    "grounding_for",
    "is_deflection",
    "run_evaluation",
]

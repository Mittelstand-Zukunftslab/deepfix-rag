"""DeepFix - Automated optimization of RAG workflow using long-running agents."""

from deepfix_rag.hpo import HPOResult, HyperparameterOpt

__version__ = "0.1.0"

__all__ = [
    "HPOResult",
    "HyperparameterOpt",
    "__version__",
]

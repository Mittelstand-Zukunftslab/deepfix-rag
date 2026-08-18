"""MLflow run logging, artifact storage, and span tracing."""

from typing import Any

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.eval.metrics import EvalResult


def log_experiment_run(
    config: RAGConfig,
    metrics: EvalResult,
    artifacts: dict[str, str] | None = None,
    span_tree: Any | None = None,
    experiment_name: str = "deepfix_rag_optimization",
) -> str:
    """Log config hyperparameters, evaluation metrics, and artifacts to MLflow."""
    # Stub returning run_id
    run_id = "mock-mlflow-run-id"
    return run_id


def log_eval_run(dataset: str, config: str, metrics: dict[str, float]) -> None:
    """Helper for CLI logging of eval run."""

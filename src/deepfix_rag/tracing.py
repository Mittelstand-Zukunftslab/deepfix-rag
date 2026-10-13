"""MLflow traces for product search and ask. A tracing failure never fails the request."""

from typing import Any

from deepfix_rag.config.settings import LOGGER

PRODUCT_EXPERIMENT = "deepfix_product"
FEEDBACK_EXPERIMENT = "deepfix_product_feedback"


def trace_request(
    name: str,
    interaction_id: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
) -> str | None:
    """Trace one request and tag it with the interaction id. Returns None when tracing fails."""
    try:
        import mlflow

        mlflow.set_experiment(PRODUCT_EXPERIMENT)
        with mlflow.start_span(name=name) as span:
            span.set_inputs(inputs)
            span.set_outputs(outputs)
            trace_id = span.trace_id
        if not trace_id:
            return None
        trace_id = str(trace_id)
        mlflow.set_trace_tag(trace_id, "interaction_id", interaction_id)
        mlflow.flush_trace_async_logging()
        return trace_id
    except Exception:
        LOGGER.exception("MLflow tracing failed for %s", interaction_id)
        return None

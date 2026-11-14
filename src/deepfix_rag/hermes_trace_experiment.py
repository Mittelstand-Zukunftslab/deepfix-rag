"""The MLflow experiment that receives Hermes agent traces.

``deepfix eval`` keeps using ``MLFLOW_DEV_RAG_EXPERIMENT``. This name is only
the trace experiment. The id is what ``hermes-otel`` sends as
``x-mlflow-experiment-id``.
"""

from __future__ import annotations

import os

HERMES_TRACE_EXPERIMENT = "deepfix_hermes"


def ensure_hermes_trace_experiment(tracking_uri: str | None = None) -> str | None:
    """Return the ``deepfix_hermes`` experiment id, creating it when it is missing.

    A tracking failure returns None. The exception text is not logged: it can
    echo a URI. The previous tracking URI and HTTP timeout are restored.
    """
    import mlflow

    previous_uri = mlflow.get_tracking_uri()
    previous_timeout = os.environ.get("MLFLOW_HTTP_REQUEST_TIMEOUT")
    os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"] = "3"
    try:
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
        existing = mlflow.get_experiment_by_name(HERMES_TRACE_EXPERIMENT)
        if existing is not None and existing.lifecycle_stage != "deleted":
            return existing.experiment_id
        return mlflow.create_experiment(HERMES_TRACE_EXPERIMENT)
    except Exception:
        return None
    finally:
        if tracking_uri:
            mlflow.set_tracking_uri(previous_uri)
        if previous_timeout is None:
            os.environ.pop("MLFLOW_HTTP_REQUEST_TIMEOUT", None)
        else:
            os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"] = previous_timeout

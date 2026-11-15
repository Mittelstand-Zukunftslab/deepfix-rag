"""The MLflow experiments that receive Hermes agent traces.

``deepfix eval`` keeps using ``MLFLOW_DEV_RAG_EXPERIMENT``. These names are only
the trace experiments. The id is what ``hermes-otel`` sends as
``x-mlflow-experiment-id``.

There are two: the optimization agent keeps ``deepfix_hermes``, and the admin
chat gets its own (``MLFLOW_HERMES_CHAT_EXPERIMENT``, default
``deepfix_hermes_chat``) so a bad answer can be read without the run history
(PRD D-5).
"""

from __future__ import annotations

import os

HERMES_TRACE_EXPERIMENT = "deepfix_hermes"
HERMES_CHAT_TRACE_EXPERIMENT = "deepfix_hermes_chat"


def ensure_hermes_trace_experiment(
    tracking_uri: str | None = None,
    name: str = HERMES_TRACE_EXPERIMENT,
) -> str | None:
    """Return the experiment id for ``name``, creating it when it is missing.

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
        existing = mlflow.get_experiment_by_name(name)
        if existing is not None and existing.lifecycle_stage != "deleted":
            return existing.experiment_id
        return mlflow.create_experiment(name)
    except Exception:
        return None
    finally:
        if tracking_uri:
            mlflow.set_tracking_uri(previous_uri)
        if previous_timeout is None:
            os.environ.pop("MLFLOW_HTTP_REQUEST_TIMEOUT", None)
        else:
            os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"] = previous_timeout

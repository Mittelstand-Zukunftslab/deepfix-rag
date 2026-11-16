"""MLflow traces for product search and ask. A tracing failure never fails the request."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from deepfix_rag.config.settings import LOGGER

PRODUCT_EXPERIMENT = "deepfix_product"
FEEDBACK_EXPERIMENT = "deepfix_product_feedback"


def trace_request(
    name: str,
    interaction_id: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    *,
    tags: dict[str, str] | None = None,
) -> str | None:
    """Trace one request and tag it with the interaction id. Returns None when tracing fails.

    ``tags`` adds the identifiers a reader needs to join a trace to its row. The
    default is none, so the traces ``ask`` and ``retrieval`` already write keep
    exactly the shape they had.
    """
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
        for key, value in (tags or {}).items():
            if value:
                mlflow.set_trace_tag(trace_id, key, value)
        mlflow.flush_trace_async_logging()
        return trace_id
    except Exception:
        LOGGER.exception("MLflow tracing failed for %s", interaction_id)
        return None


class TurnTrace:
    """One chat turn's trace, kept open while the turn runs so searches can nest.

    MLflow nests a span through the ambient context, not through a ``trace_id``
    argument, so a child span is only a child while its parent is open. That is
    why a turn's trace is a context manager rather than the open-and-close
    ``trace_request``: the tool calls happen between the parent's open and close.

    Every method swallows and logs its own failure, because a member's answer must
    never depend on MLflow being reachable (FR-020).
    """

    def __init__(self, trace_id: str | None, span: Any) -> None:
        self.trace_id = trace_id
        self._span = span

    def tool_call(
        self,
        *,
        query: str,
        passage_count: int,
        max_score: float,
        duration_ms: int,
    ) -> None:
        """Record one ``search_documents`` call: its query, its count, its numbers.

        No passage text is written, so the trace describes the search rather than
        copying the conversation (NFR-5).
        """
        try:
            import mlflow

            with mlflow.start_span(name="search_documents") as span:
                span.set_inputs({"query": query})
                span.set_outputs(
                    {
                        "passage_count": passage_count,
                        "max_score": max_score,
                        "duration_ms": duration_ms,
                    }
                )
        except Exception:
            LOGGER.exception("MLflow tool span failed for this turn")

    def outputs(self, values: dict[str, Any]) -> None:
        """Set the turn's own outputs, once the answer is known."""
        try:
            if self._span is not None:
                self._span.set_outputs(values)
        except Exception:
            LOGGER.exception("MLflow turn outputs failed for this turn")

    def tag(self, key: str, value: str | None) -> None:
        """Tag the trace, so a row can be found by its turn or its terminal status."""
        if not value:
            return
        try:
            import mlflow

            if self.trace_id is not None:
                mlflow.set_trace_tag(self.trace_id, key, value)
        except Exception:
            LOGGER.exception("MLflow turn tag failed for this turn")

    def failed(self, reason: str) -> None:
        """Mark the trace ``ERROR``, which is the trace's state rather than a tag.

        A failed turn must not read as a healthy trace in the MLflow UI: the tag
        says what the status was, and this says the trace itself failed, so a
        reviewer filtering by state sees it. ``LiveSpan.set_status`` takes the
        status alone — the description goes on the span's attributes, because this
        MLflow version's signature has no second argument. Best effort, like every
        other call here: a member's answer must not depend on MLflow.
        """
        try:
            if self._span is None:
                return
            self._span.set_attributes({"deepfix.failure": reason})
            self._span.set_status("ERROR")
        except Exception:
            LOGGER.exception("MLflow turn status failed for this turn")


@contextmanager
def turn_trace(
    interaction_id: str,
    inputs: dict[str, Any],
    tags: dict[str, str],
) -> Iterator[TurnTrace]:
    """Open one chat turn's trace, tag it, and close it whatever happens.

    Yields a :class:`TurnTrace` even when tracing is unavailable — with a ``None``
    trace id — so the caller never branches on whether MLflow is up. A failure to
    *open* the trace is logged and the turn runs untraced; a failure from the
    caller's own body is re-raised, because swallowing it here would hide a real
    turn failure behind a tracing concern.
    """
    try:
        import mlflow

        mlflow.set_experiment(PRODUCT_EXPERIMENT)
        span = mlflow.start_span(name="ask_chat")
    except Exception:
        LOGGER.exception("MLflow tracing failed for %s", interaction_id)
        yield TurnTrace(None, None)
        return
    with span as live:
        trace_id = str(live.trace_id) if live.trace_id else None
        trace = TurnTrace(trace_id, live)
        live.set_inputs(inputs)
        if trace_id is not None:
            try:
                mlflow.set_trace_tag(trace_id, "interaction_id", interaction_id)
                for key, value in tags.items():
                    if value:
                        mlflow.set_trace_tag(trace_id, key, value)
            except Exception:
                LOGGER.exception("MLflow turn tags failed for this turn")
        try:
            yield trace
        finally:
            try:
                mlflow.flush_trace_async_logging()
            except Exception:
                LOGGER.exception("MLflow flush failed for this turn")

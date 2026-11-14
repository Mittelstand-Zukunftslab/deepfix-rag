"""Rerank through OpenRouter's rerank API.

OpenRouter serves rerank at ``POST {base}/rerank`` on the OpenAI-compatible
base (``https://openrouter.ai/api/v1`` becomes
``https://openrouter.ai/api/v1/rerank``). The Cohere SDK appends
``/v2/rerank`` and 404s against that base, so this client is owned here and
speaks the documented request shape directly:

* body: ``documents`` (non-empty list of strings), ``model``, ``query``, and
  ``top_n`` when a final list size is configured
* headers: ``Authorization: Bearer <OPENROUTER_API_KEY>`` and
  ``Content-Type: application/json``
* response: ``results[]`` with ``index`` (the document offset) and
  ``relevance_score`` (``document.text`` is optional)

Native Cohere (``https://api.cohere.com`` + ``COHERE_API_KEY``) is not used.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from pydantic import Field, PrivateAttr

from deepfix_rag.config.settings import LOGGER, settings

RERANK_PATH = "/rerank"
DEFAULT_TIMEOUT_SECONDS = 60.0


def rerank_endpoint(base_url: str) -> str:
    """The rerank URL for an OpenAI-compatible base.

    ``https://openrouter.ai/api/v1`` -> ``https://openrouter.ai/api/v1/rerank``.
    A host-only base gets OpenRouter's documented ``/api/v1/rerank``. This
    never appends ``/v2/rerank``.
    """
    parts = urlsplit(base_url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"COHERE_BASE_URL is not an absolute URL: {base_url!r}")
    path = parts.path.rstrip("/")
    if path.endswith(RERANK_PATH):
        resolved = path
    elif path.endswith("/v1"):
        resolved = path + RERANK_PATH
    elif not path:
        resolved = "/api/v1" + RERANK_PATH
    else:
        resolved = path + RERANK_PATH
    return urlunsplit((parts.scheme, parts.netloc, resolved, "", ""))


def rerank_api_key() -> str | None:
    """The OpenRouter key the rerank call authorizes with.

    ``OPENROUTER_API_KEY`` first, then a legacy ``COHERE_API_KEY`` that already
    holds an OpenRouter key. ``LLM_API_KEY`` is not a fallback: on a
    non-OpenRouter model endpoint it is a different provider's key and must not
    be sent to OpenRouter.
    """
    for name in ("OPENROUTER_API_KEY", "COHERE_API_KEY"):
        value = (getattr(settings, name, "") or "").strip()
        if value:
            return value
    return None


class RerankResponseError(RuntimeError):
    """OpenRouter answered a rerank request with an error or an unusable body."""


class OpenRouterReranker(BaseNodePostprocessor):
    """Reorder retrieved nodes by OpenRouter's relevance scores."""

    model: str
    top_n: int = Field(default=5, ge=1)
    api_key: str = Field(default="", exclude=True, repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    _last_endpoint: str | None = PrivateAttr(default=None)
    _last_status: int | None = PrivateAttr(default=None)

    @classmethod
    def class_name(cls) -> str:
        return "OpenRouterReranker"

    @property
    def endpoint(self) -> str:
        """The URL this reranker posts to."""
        return rerank_endpoint(self.base_url)

    def _request(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        endpoint = self.endpoint
        self._last_endpoint = endpoint
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(endpoint, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise RerankResponseError(
                f"rerank request to {endpoint} failed: {type(exc).__name__}"
            ) from exc
        self._last_status = response.status_code
        if response.status_code >= 400:
            raise RerankResponseError(
                f"rerank request to {endpoint} returned HTTP {response.status_code}: "
                f"{_error_message(response)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RerankResponseError(f"rerank response from {endpoint} was not JSON") from exc
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise RerankResponseError(f"rerank response from {endpoint} has no results list")
        return results

    def _postprocess_nodes(
        self,
        nodes: list[NodeWithScore],
        query_bundle: QueryBundle | None = None,
    ) -> list[NodeWithScore]:
        if not nodes or query_bundle is None:
            return list(nodes)
        payload: dict[str, Any] = {
            "documents": [node.node.get_content() for node in nodes],
            "model": self.model,
            "query": query_bundle.query_str,
            "top_n": min(self.top_n, len(nodes)),
        }
        results = self._request(payload)
        ranked = _rank(nodes, results)
        LOGGER.debug(f"Reranked {len(ranked)}/{len(nodes)} nodes with {self.model}")
        return ranked[: self.top_n]


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return response.text[:200] or response.reason_phrase


def _rank(
    nodes: list[NodeWithScore],
    results: list[dict[str, Any]],
) -> list[NodeWithScore]:
    """Order nodes by ``relevance_score``. A result without a usable index is skipped."""
    scored: list[tuple[float, int, NodeWithScore]] = []
    for position, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if index < 0 or index >= len(nodes):
            continue
        raw_score = item.get("relevance_score")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
        scored.append((score, position, NodeWithScore(node=nodes[index].node, score=score)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [node for _score, _position, node in scored]

"""The OpenRouter rerank client against a local HTTP server.

No live network and no mock of domain code: a real ``http.server`` records the
request and answers with the shape the OpenRouter documentation shows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
import pytest

from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.config.settings import ModelNotConfigured, require_model, settings
from deepfix_rag.pipeline import RAGWorkflow
from deepfix_rag.rerank import (
    OpenRouterReranker,
    RerankResponseError,
    rerank_endpoint,
)

pytestmark = [pytest.mark.unit]

MODEL = "cohere/rerank-4-fast"
KEY = "sk-or-test-rerank-key"


class _Recorder:
    """Requests this test server received, in order."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: dict[str, Any] = {
            "id": "gen-rerank-test",
            "model": MODEL,
            "results": [
                {"document": {"text": "second"}, "index": 1, "relevance_score": 0.91},
                {"document": {"text": "first"}, "index": 0, "relevance_score": 0.12},
            ],
            "usage": {"search_units": 1, "total_tokens": 12},
        }


@contextmanager
def _server(recorder: _Recorder) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            recorder.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "json": json.loads(raw or b"{}"),
                }
            )
            payload = json.dumps(recorder.body).encode()
            self.send_response(recorder.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _nodes() -> list[NodeWithScore]:
    return [
        NodeWithScore(node=TextNode(id_="a", text="first"), score=0.9),
        NodeWithScore(node=TextNode(id_="b", text="second"), score=0.8),
    ]


def test_rerank_endpoint_never_appends_the_cohere_v2_path() -> None:
    assert rerank_endpoint("https://openrouter.ai/api/v1") == (
        "https://openrouter.ai/api/v1/rerank"
    )
    assert rerank_endpoint("https://openrouter.ai") == "https://openrouter.ai/api/v1/rerank"
    assert rerank_endpoint("https://openrouter.ai/api/v1/") == (
        "https://openrouter.ai/api/v1/rerank"
    )
    assert rerank_endpoint("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1/rerank"
    for base in (
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/",
        "https://openrouter.ai",
        "http://localhost:9/v1",
    ):
        resolved = rerank_endpoint(base)
        assert "/v2/rerank" not in resolved
        assert resolved.endswith("/rerank")


def test_the_reranker_posts_the_documented_body_and_orders_by_relevance() -> None:
    recorder = _Recorder()
    with _server(recorder) as base:
        reranker = OpenRouterReranker(model=MODEL, top_n=2, api_key=KEY, base_url=f"{base}/v1")
        ranked = reranker.postprocess_nodes(_nodes(), query_str="capital of France?")

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/v1/rerank"
    assert request["path"] != "/v1/v2/rerank"
    assert request["headers"]["authorization"] == f"Bearer {KEY}"
    assert "application/json" in request["headers"]["content-type"]
    assert request["json"]["documents"] == ["first", "second"]
    assert request["json"]["model"] == MODEL
    assert request["json"]["query"] == "capital of France?"
    assert request["json"]["top_n"] == 2

    assert [node.node.get_content() for node in ranked] == ["second", "first"]
    assert [node.score for node in ranked] == [0.91, 0.12]


def test_top_n_is_omitted_free_but_capped_at_the_candidate_count() -> None:
    recorder = _Recorder()
    with _server(recorder) as base:
        reranker = OpenRouterReranker(model=MODEL, top_n=50, api_key=KEY, base_url=base)
        reranker.postprocess_nodes(_nodes(), query_str="q")
    assert recorder.requests[0]["json"]["top_n"] == 2
    assert recorder.requests[0]["path"] == "/api/v1/rerank"


def test_a_result_without_an_index_is_ignored() -> None:
    recorder = _Recorder()
    recorder.body["results"] = [
        {"relevance_score": 0.99, "document": {"text": "no index"}},
        {"index": 1, "relevance_score": 0.5},
    ]
    with _server(recorder) as base:
        reranker = OpenRouterReranker(model=MODEL, top_n=5, api_key=KEY, base_url=base)
        ranked = reranker.postprocess_nodes(_nodes(), query_str="q")
    assert [node.node.get_content() for node in ranked] == ["second"]


def test_an_http_error_names_the_endpoint_and_status() -> None:
    recorder = _Recorder()
    recorder.status = 404
    recorder.body = {"error": {"code": 404, "message": "Resource not found"}}
    with _server(recorder) as base:
        reranker = OpenRouterReranker(model=MODEL, top_n=5, api_key=KEY, base_url=base)
        with pytest.raises(RerankResponseError, match="HTTP 404") as caught:
            reranker.postprocess_nodes(_nodes(), query_str="q")
    assert "/api/v1/rerank" in str(caught.value)
    assert "Resource not found" in str(caught.value)


@contextmanager
def _rerank_settings(base_url: str) -> Iterator[None]:
    previous = (
        settings.COHERE_MODEL,
        settings.COHERE_BASE_URL,
        settings.OPENROUTER_API_KEY,
        settings.COHERE_API_KEY,
    )
    settings.COHERE_MODEL = MODEL
    settings.COHERE_BASE_URL = base_url
    settings.OPENROUTER_API_KEY = KEY
    settings.COHERE_API_KEY = None
    try:
        yield
    finally:
        (
            settings.COHERE_MODEL,
            settings.COHERE_BASE_URL,
            settings.OPENROUTER_API_KEY,
            settings.COHERE_API_KEY,
        ) = previous


def test_the_workflow_reranker_uses_openrouter_and_needs_no_cohere_key() -> None:
    recorder = _Recorder()
    with _server(recorder) as base, _rerank_settings(f"{base}/v1"):
        config = RAGConfig(rerank={"enabled": True, "top_n": 2}, eval={"cutoff_k": 2})
        workflow = RAGWorkflow(config=config)
        reranker = workflow._get_reranker()
        assert isinstance(reranker, OpenRouterReranker)
        reranker.postprocess_nodes(_nodes(), query_str="q")

    assert recorder.requests[0]["path"] == "/v1/rerank"
    assert recorder.requests[0]["headers"]["authorization"] == f"Bearer {KEY}"
    assert recorder.requests[0]["json"]["model"] == MODEL


def test_the_workflow_reranker_names_the_openrouter_key_when_it_is_missing() -> None:
    with _rerank_settings("https://openrouter.ai/api/v1"):
        settings.OPENROUTER_API_KEY = ""
        settings.COHERE_API_KEY = ""
        config = RAGConfig(rerank={"enabled": True, "top_n": 2}, eval={"cutoff_k": 2})
        with pytest.raises(ModelNotConfigured, match="OPENROUTER_API_KEY"):
            RAGWorkflow(config=config)._get_reranker()


def test_rerank_scores_a_retrieval_only_run_through_the_local_server(
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """A full retrieval-only eval with rerank enabled reaches the local endpoint."""
    recorder = _Recorder()
    index_dir = tmp_path / "rerank_index"
    config = RAGConfig(rerank={"enabled": True, "top_n": 2}, eval={"cutoff_k": 2})
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.retrieval.top_k = 3
    config.retrieval_only = True
    config.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))

    with _server(recorder) as base, _rerank_settings(f"{base}/v1"):
        recorder.body["results"] = [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]

        async def run() -> None:
            workflow = RAGWorkflow(config=config, embed_model=MockEmbedding(embed_dim=16))
            index = await workflow.run(ingest=True)
            result = await workflow.run(query="Straßenverkehr", index=index)
            assert result.retrieval_results
            assert len(result.retrieval_results) <= 2

        asyncio.run(run())

    assert recorder.requests, "the rerank endpoint was never called"
    for request in recorder.requests:
        assert request["path"] == "/v1/rerank"
        assert isinstance(request["json"]["documents"], list)
        assert request["json"]["documents"]


@pytest.mark.unit
def test_require_model_still_names_cohere_model() -> None:
    with _rerank_settings("https://openrouter.ai/api/v1"):
        settings.COHERE_MODEL = ""
        with pytest.raises(ModelNotConfigured, match="COHERE_MODEL"):
            require_model("COHERE_MODEL")

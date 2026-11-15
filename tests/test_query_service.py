"""Tests for the long-lived query service."""

import asyncio
from pathlib import Path
from typing import get_type_hints

from llama_index.core.base.llms.types import ChatMessage, ChatResponse, CompletionResponse
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
import pytest

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.pipeline import RAGResult, RAGWorkflow
from deepfix_rag.service import QueryService

pytestmark = pytest.mark.integration


class CountingLLM(MockLLM):
    """Local generation model that records every completion or chat call."""

    def complete(self, prompt: str, formatted: bool = False, **kwargs) -> CompletionResponse:
        self.metadata  # touch pydantic state
        calls.append("complete")
        return CompletionResponse(text="grounded answer")

    def chat(self, messages, **kwargs) -> ChatResponse:
        calls.append("chat")
        return ChatResponse(message=ChatMessage(role="assistant", content="grounded answer"))


calls: list[str] = []


@pytest.mark.unit
def test_generated_answer_is_str_or_none() -> None:
    """The RAG result uses a modern optional annotation."""
    assert get_type_hints(RAGResult)["generated_answer"] == str | None


def test_retrieve_twice_loads_the_index_once(
    sample_rag_config: RAGConfig,
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """Retrieval and answering share the index loaded at construction."""
    index_dir = tmp_path / "sample_lancedb_index"
    config = sample_rag_config.model_copy(deep=True)
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.data.path_or_repo = str(sample_corpus_path)
    config.retrieval_only = True
    embed_model = MockEmbedding(embed_dim=16)
    llm = CountingLLM()
    calls.clear()

    async def build() -> None:
        workflow = RAGWorkflow(config=config, embed_model=embed_model, llm=llm)
        await workflow.run(ingest=True)

    asyncio.run(build())
    calls.clear()

    service = QueryService(config, index_dir, embed_model=embed_model, llm=llm)
    assert service.index_load_count == 1

    async def ask_twice() -> None:
        first = await service.retrieve("Straßenverkehr")
        second = await service.retrieve("Haftung des Fahrzeughalters")
        assert calls == []
        assert first
        assert second
        for hit in first:
            assert hit.doc_id.startswith("doc_de_")
            assert hit.text
            assert isinstance(hit.score, float)
        answered = await service.answer("Was regelt die Trunkenheit im Straßenverkehr?")
        assert answered.retrieval_results
        assert answered.generated_answer == "grounded answer"
        assert calls

    asyncio.run(ask_twice())
    assert service.index_load_count == 1


def test_the_relevance_floor_skips_generation_and_keeps_the_dropped_hits(
    sample_rag_config: RAGConfig,
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """The floor is the same gate the eval path runs through, and it is a floor, not a filter on display."""
    index_dir = tmp_path / "sample_lancedb_index"
    config = sample_rag_config.model_copy(deep=True)
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.data.path_or_repo = str(sample_corpus_path)
    config.retrieval_only = True
    embed_model = MockEmbedding(embed_dim=16)

    async def build() -> None:
        workflow = RAGWorkflow(config=config, embed_model=embed_model, llm=CountingLLM())
        await workflow.run(ingest=True)

    asyncio.run(build())
    calls.clear()

    service = QueryService(config, index_dir, embed_model=embed_model, llm=CountingLLM())
    service.config.rerank.enabled = True
    service.config.rerank.min_relevance_score = 0.99
    service._workflow.config.rerank.enabled = True
    service._workflow.config.rerank.min_relevance_score = 0.99
    service._workflow._get_reranker = lambda: _FlatScores()  # type: ignore[method-assign]

    async def ask() -> RAGResult:
        return await service.answer("Was regelt die Trunkenheit im Straßenverkehr?")

    answered = asyncio.run(ask())
    assert answered.generated_answer is None
    assert answered.retrieval_results == []
    assert answered.dropped_results
    assert calls == [], "nothing above the floor means no generation call"

    service.config.rerank.min_relevance_score = 0.0
    service._workflow.config.rerank.min_relevance_score = 0.0
    kept = asyncio.run(ask())
    assert kept.retrieval_results
    assert kept.dropped_results == []
    assert kept.generated_answer == "grounded answer"


class _FlatScores(BaseNodePostprocessor):
    """Scores every candidate at 0.5, so one floor value decides the whole list."""

    @classmethod
    def class_name(cls) -> str:
        return "FlatScores"

    def _postprocess_nodes(
        self,
        nodes: list[NodeWithScore],
        query_bundle: QueryBundle | None = None,
    ) -> list[NodeWithScore]:
        return [NodeWithScore(node=node.node, score=0.5) for node in nodes]

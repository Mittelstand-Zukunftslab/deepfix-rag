"""Tests for the long-lived query service."""

import asyncio
from pathlib import Path
from typing import get_type_hints

from llama_index.core.base.llms.types import ChatMessage, ChatResponse, CompletionResponse
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
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

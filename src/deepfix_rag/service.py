"""Long-lived query service that loads one index and answers retrieval or RAG calls."""

from pathlib import Path

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.indices.base import BaseIndex
from llama_index.core.schema import NodeWithScore

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.pipeline import RAGResult, RAGWorkflow, RetrievalResult


class QueryService:
    """Keep a built index in memory and serve retrieval and answer requests from it.

    ``retrieve`` never calls the generation model, even when ``config.retrieval_only``
    is false. ``answer`` always synthesizes, even when that flag is true.
    """

    def __init__(
        self,
        config: RAGConfig,
        index_dir: Path | str,
        *,
        embed_model: BaseEmbedding | None = None,
        llm: BaseLLM | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.config.retrieval.lancedb_index_dir = str(index_dir)
        self.config.retrieval.load_if_exists = True
        self.index_load_count = 0
        self._workflow = RAGWorkflow(config=self.config, embed_model=embed_model, llm=llm)
        self._index = self._load_index()

    def _load_index(self) -> BaseIndex:
        index_dir = self.config.retrieval.lancedb_index_dir
        if not self._workflow._index_exists():
            raise FileNotFoundError(f"No index at {index_dir}.")
        self.index_load_count += 1
        index = self._workflow._load_existing_index()
        self._workflow._index = index
        return index

    async def retrieve(self, query: str) -> list[RetrievalResult]:
        """Return ranked passages. Does not call the generation model."""
        nodes = await self._search(query)
        return self._workflow.convert_nodes_to_results(nodes)

    async def answer(self, query: str) -> RAGResult:
        """Return ranked passages and a generated answer."""
        nodes = await self._search(query)
        generated = await self._generate(query, nodes)
        return RAGResult(
            retrieval_results=self._workflow.convert_nodes_to_results(nodes),
            generated_answer=generated,
        )

    async def _search(self, query: str) -> list[NodeWithScore]:
        retriever = self._workflow._build_retriever(
            index=self._index,
            top_k=self.config.retrieval.top_k,
            language=self.config.retrieval.language,
            use_async=True,
        )
        nodes = await retriever.aretrieve(query)
        reranker = self._workflow._get_reranker()
        if reranker is None:
            return nodes
        return reranker.postprocess_nodes(nodes, query_str=query)

    async def _generate(self, query: str, nodes: list[NodeWithScore]) -> str | None:
        try:
            synthesizer = self._workflow._get_response_synthesizer()
            response = await synthesizer.asynthesize(query, nodes=nodes)
        except Exception:
            LOGGER.exception("Generation failed for query %r", query)
            return None
        if response is None or response.response is None:
            return None
        text = str(response.response).strip()
        return text or None

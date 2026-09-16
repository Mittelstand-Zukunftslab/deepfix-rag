"""RAG retrieval and generation execution powered by LlamaIndex Workflows."""

import asyncio
import logging
from typing import Any

from llama_index.core import (
    Document,
    PropertyGraphIndex,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.graph_stores.types import GraphStore
from llama_index.core.indices.base import BaseIndex
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor
from llama_index.core.response_synthesizers import CompactAndRefine
from llama_index.core.schema import NodeWithScore
from llama_index.core.vector_stores.types import BasePydanticVectorStore
from llama_index.core.workflow import (
    Context,
    Event,
    StartEvent,
    StopEvent,
    Workflow,
    step,
)
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.graph_stores.falkordb import FalkorDBGraphStore
from llama_index.graph_stores.memgraph import MemgraphPropertyGraphStore
from llama_index.llms.litellm import LiteLLM
from llama_index.postprocessor.cohere_rerank import CohereRerank
from llama_index.vector_stores.lancedb import LanceDBVectorStore
from pydantic import BaseModel, Field
from turbovec.llama_index import TurboQuantVectorStore

from deepfix_rag.config.schema import GraphStoreType, RAGConfig, VectorStoreType
from deepfix_rag.config.settings import settings

from .data import DataLoader

logger = logging.getLogger(__name__)


class RetrieverEvent(Event):
    """Result of running retrieval."""

    nodes: list[NodeWithScore]


class RerankEvent(Event):
    """Result of running reranking on retrieved nodes."""

    nodes: list[NodeWithScore]


class RetrievalResult(BaseModel):
    """Retrieved document candidate."""

    doc_id: str = Field(..., description="Unique identifier for the document")
    score: float = Field(..., description="Retrieval score")
    text: str = Field(..., description="The text of the document")


class RAGWorkflow(Workflow):
    """Event-driven RAG Workflow supporting Ingestion, Retrieval, Reranking, and Synthesis."""

    def __init__(
        self,
        config: RAGConfig | None = None,
        index_dir: str | None = None,
        llm: BaseLLM | None = None,
        embed_model: BaseEmbedding | None = None,
        streaming: bool = False,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.config = config or RAGConfig()
        self.index_dir = index_dir
        self.llm = llm
        self.embed_model = embed_model
        self.streaming = streaming

    def _get_embed_model(self) -> BaseEmbedding:
        if self.embed_model is not None:
            return self.embed_model
        return OpenAIEmbedding(
            model_name=self.config.embedding.model,
            api_base=settings.OCTEN_URL
            if self.config.embedding.provider == "aisc"
            else settings.LITELLM_URL,
            api_key=settings.OCTEN_KEY or settings.LITELLM_KEY,
        )

    def _get_llm(self) -> BaseLLM:
        if self.llm is not None:
            return self.llm
        return LiteLLM(
            model=settings.LLM_MODEL,
            temperature=self.config.generation.temperature,
            max_tokens=self.config.generation.max_tokens,
            api_base=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            system_prompt=self.config.generation.system_prompt,
        )

    def _get_reranker(self) -> CohereRerank | None:
        if not self.config.rerank.enabled:
            return None
        return CohereRerank(
            model=settings.COHERE_MODEL,
            top_n=self.config.rerank.top_n,
            api_key=settings.COHERE_API_KEY,
            base_url=settings.COHERE_BASE_URL,
        )

    def _get_vector_store(self) -> BasePydanticVectorStore:
        if self.config.retrieval.vector_store == VectorStoreType.LANCEDB:
            return LanceDBVectorStore(uri=self.index_dir, table_name="documents")
        elif self.config.retrieval.vector_store == VectorStoreType.TURBO_QUANT:
            return TurboQuantVectorStore.from_params(bit_width=self.config.retrieval.bit_width)
        else:
            raise ValueError("Invalid vector store")

    def _get_graph_store(self) -> GraphStore:
        if self.config.retrieval.graph_store == GraphStoreType.MEMGRAPH:
            return MemgraphPropertyGraphStore(
                url=settings.MEMGRAPH_URL,
                username=settings.MEMGRAPH_USERNAME,
                password=settings.MEMGRAPH_PASSWORD,
            )
        elif self.config.retrieval.graph_store == GraphStoreType.FALKORDB:
            return FalkorDBGraphStore(
                hostname=settings.FALKORDB_HOST,
                port=settings.FALKORDB_PORT,
                username=settings.FALKORDB_USERNAME,
                password=settings.FALKORDB_PASSWORD,
                database=settings.FALKORDB_DATABASE,
            )
        return None

    def _get_storage_context(self, persist_dir=None) -> StorageContext:
        graph_store = self._get_graph_store()
        vector_store = self._get_vector_store()
        storage_context = StorageContext.from_defaults(
            persist_dir=persist_dir,
            graph_store=graph_store
            if self.config.retrieval.graph_store == GraphStoreType.FALKORDB
            else None,
            index_store=None,
            vector_store=vector_store,
            property_graph_store=graph_store
            if self.config.retrieval.graph_store == GraphStoreType.MEMGRAPH
            else None,
            image_store=None,  # images of scanned PDFs
        )

        return storage_context

    def _build_index(self, documents: list[Document], storage_context=None, **kwargs) -> BaseIndex:
        if storage_context is None:
            storage_context = self._get_storage_context()

        if self.config.retrieval.graph_store != GraphStoreType.NONE:
            index = PropertyGraphIndex.from_documents(
                documents,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
                kg_extractors=[
                    SchemaLLMPathExtractor(
                        llm=self._get_llm(),
                    )
                ],
                show_progress=True,
            )
        else:
            index = VectorStoreIndex.from_documents(
                documents,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
            )

        return index

    @step
    async def ingest(self, ctx: Context, ev: StartEvent) -> StopEvent | None:
        """Entry point to ingest documents into vector/graph index."""
        data_config = ev.get("data_config")
        if not data_config:
            raise ValueError("You must pass a 'data_config' DataLoaderConfig to ingest.")

        data_loader = DataLoader()
        documents = data_loader.load_documents(data_config)

        index = self._build_index(documents)
        await ctx.store.set("index", index)
        return StopEvent(result=index)

    @step
    async def retrieve(self, ctx: Context, ev: StartEvent) -> RetrieverEvent:
        """Entry point for RAG, triggered by a StartEvent with `query`."""
        query = ev.get("query")
        index: PropertyGraphIndex | VectorStoreIndex = await ctx.store.get("index")

        if not query:
            raise ValueError("Query is empty. Please provide a query.")

        if index is None:
            raise ValueError("No index found. Please ingest documents first.")

        # store the query in the global context
        logger.debug(f"Query: {query}")
        await ctx.store.set("query", query)

        kwargs = {}
        if isinstance(index, PropertyGraphIndex):
            kwargs["response_mode"] = "tree_summarize"
            kwargs["embedding_mode"] = "hybrid"
        elif isinstance(index, VectorStoreIndex):
            pass

        retriever = index.as_retriever(
            similarity_top_k=self.config.retrieval.top_k,
            **kwargs,
        )
        nodes = await retriever.aretrieve(query)

        logger.debug(f"Retrieved {len(nodes)} nodes")

        return RetrieverEvent(nodes=nodes)

    @step
    async def rerank(self, ctx: Context, ev: RetrieverEvent) -> RerankEvent:
        """Rerank candidate nodes using CohereRerank with custom base_url."""
        query = await ctx.store.get("query")
        nodes = ev.nodes
        reranker = self._get_reranker()

        if reranker is not None:
            try:
                nodes = reranker.postprocess_nodes(nodes, query_str=query)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"CohereRerank failed: {e}. Falling back to top_n un-reranked nodes."
                )
                nodes = nodes[: self.config.rerank.top_n]

        if self.config.retrieval_only:
            return RerankEvent(nodes=nodes)

        return RerankEvent(nodes=nodes)

    @step
    async def synthesize(self, ctx: Context, ev: RerankEvent) -> StopEvent:
        """Synthesize response using CompactAndRefine."""
        query = await ctx.store.get("query")
        if query is None:
            raise ValueError("No query found. Please ingest documents first.")

        summarizer = CompactAndRefine(llm=self._get_llm(), streaming=False, verbose=False)

        response = await summarizer.asynthesize(query, nodes=ev.nodes)
        return StopEvent(result=response)


def convert_nodes_to_results(nodes: list[NodeWithScore]) -> list[RetrievalResult]:
    """Convert a list of NodeWithScore objects to standardized RetrievalResult models."""
    results = []
    for node in nodes:
        doc_id_val = node.node.metadata.get("doc_id", node.node.node_id)
        results.append(
            RetrievalResult(
                doc_id=doc_id_val,
                score=node.score or 0.0,
                text=node.node.get_content(),
            )
        )
    return results


async def aexecute_retrieval(
    query: str,
    config: RAGConfig,
    index_dir: str | None = None,
    workflow: RAGWorkflow | None = None,
) -> list[RetrievalResult]:
    """Asynchronously execute retrieval using RAGWorkflow in retrieval_only mode."""

    # override retrieval_only
    config.retrieval_only = True

    wf = workflow or RAGWorkflow(config=config, index_dir=index_dir)
    results = await wf.run(
        query=query,
    )
    return convert_nodes_to_results(results.nodes)


def execute_retrieval(
    query: str,
    config: RAGConfig,
    index_dir: str | None = None,
    workflow: RAGWorkflow | None = None,
) -> list[RetrievalResult]:
    """Execute retrieval using LlamaIndex VectorStore engine via RAGWorkflow."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                aexecute_retrieval(query, config, index_dir=index_dir, workflow=workflow),
            )
            return future.result()
    else:
        return asyncio.run(
            aexecute_retrieval(query, config, index_dir=index_dir, workflow=workflow)
        )

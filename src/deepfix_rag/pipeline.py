"""RAG retrieval and generation execution powered by LlamaIndex Workflows."""

import logging
from typing import Any

from llama_index.core import (
    Document,
    PropertyGraphIndex,
    StorageContext,
    VectorStoreIndex,
)
import lancedb

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.graph_stores.types import GraphStore
from llama_index.core.indices.base import BaseIndex
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor
from llama_index.core.response_synthesizers import CompactAndRefine
from llama_index.core.retrievers import QueryFusionRetriever
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
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.lancedb import LanceDBVectorStore
from pydantic import BaseModel, Field
import Stemmer
from turbovec.llama_index import TurboQuantVectorStore

from deepfix_rag.config.schema import GraphStoreType, RAGConfig, VectorStoreType
from deepfix_rag.config.settings import settings
from deepfix_rag.data import DataLoader

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
        self.index_dir = index_dir or settings.S3_URI
        self.llm = llm
        self.embed_model = embed_model
        self.streaming = streaming

    def _get_embed_model(self) -> BaseEmbedding:
        if self.embed_model is not None:
            return self.embed_model
        return OpenAIEmbedding(
            model_name=settings.EMBEDDING_MODEL,
            api_base=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
        )

    def _get_llm(self) -> BaseLLM:
        if self.llm is not None:
            return self.llm
        model_name = settings.LLM_MODEL
        if not (model_name.startswith("openai/") or "/" in model_name):
            model_name = f"openai/{model_name}"
        return LiteLLM(
            model=model_name,
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
            index = LanceDBVectorStore(uri=settings.S3_URI, table_name="documents")
            index._connection = lancedb.connect(
                uri=settings.S3_URI,
                storage_options={
                    "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
                    "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
                    "aws_region": settings.S3_REGION,
                },
            )
            return index
                   
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
        return StorageContext.from_defaults(
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
    
    def _build_retriever(self,index:VectorStoreIndex, top_k:int=5, language:str="german", use_async:bool=True):
        assert isinstance(index, VectorStoreIndex), f"Expected VectorStoreIndex, got {type(index)}"
        if self.config.retrieval.hybrid:
            retriever = QueryFusionRetriever(
            [
                index.as_retriever(similarity_top_k=top_k),
                BM25Retriever.from_defaults(
                    index=index, 
                    similarity_top_k=top_k,
                    language=language, 
                    stemmer=Stemmer.Stemmer("german")
                ),
            ],
            num_queries=1, # This is for the query expansion, set to 1 to disable it
            use_async=use_async,
            similarity_top_k=top_k,
            )
        else:
            retriever = index.as_retriever(similarity_top_k=top_k)
        return retriever

    @step
    async def process_start(self, ctx: Context, ev: StartEvent) -> StopEvent:
        """Entry point for both Ingestion and Retrieval."""
        data_config = ev.get("data_config")

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

       
        if isinstance(index, PropertyGraphIndex):
            kwargs = {}
            kwargs["response_mode"] = "tree_summarize"
            kwargs["embedding_mode"] = "hybrid"
            retriever = index.as_rSetriever(
            similarity_top_k=self.config.retrieval.top_k,
            **kwargs,
            )
        elif isinstance(index, VectorStoreIndex):        
            retriever = self._build_retriever(
                index=index,
                top_k=self.config.retrieval.top_k,
                language=self.config.retrieval.language,
                use_async=True,
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
            except Exception as e:
                logger.warning(
                    f"CohereRerank failed: {e}. Falling back to top_n un-reranked nodes."
                )
                nodes = nodes[: self.config.rerank.top_n]

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
                doc_id=str(doc_id_val),
                score=node.score or 0.0,
                text=node.node.get_content(),
            )
        )
    return results

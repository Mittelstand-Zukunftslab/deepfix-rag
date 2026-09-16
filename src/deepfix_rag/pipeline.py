import asyncio
import concurrent.futures
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
from llama_index.core.response_synthesizers import (
    Refine,
    CompactAndRefine,
    SimpleSummarize,
    TreeSummarize,
)
from llama_index.core.response_synthesizers.base import (
    BaseSynthesizer,
)
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, TextNode
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

from deepfix_rag.config.schema import (
    GraphStoreType,
    RAGConfig,
    ResponseSynthesizerType,
    RetrievalBackend,
    VectorStoreType,
)
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


class CustomLiteLLM(LiteLLM):
    custom_context_window: int = 20000

    @property
    def metadata(self):
        meta = super().metadata
        meta.context_window = self.custom_context_window
        return meta


class RAGWorkflow(Workflow):
    """Event-driven RAG Workflow supporting Ingestion, Retrieval, Reranking, and Synthesis."""

    def __init__(
        self,
        config: RAGConfig | None = None,
        llm: BaseLLM | None = None,
        embed_model: BaseEmbedding | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.config = config or RAGConfig()
        self.llm = llm
        self.embed_model = embed_model

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
        return CustomLiteLLM(
            model=model_name,
            temperature=self.config.generation.temperature,
            max_tokens=self.config.generation.max_tokens,
            api_base=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            system_prompt=self.config.generation.system_prompt,
            context_window=self.config.generation.context_window,
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
            uri = self.config.retrieval.lancedb_index_dir
            storage_options: dict[str, str] = {}

            if uri.startswith("s3://"):
                if settings.S3_ACCESS_KEY_ID:
                    storage_options["aws_access_key_id"] = str(settings.S3_ACCESS_KEY_ID)
                if settings.S3_SECRET_ACCESS_KEY:
                    storage_options["aws_secret_access_key"] = str(settings.S3_SECRET_ACCESS_KEY)
                if settings.S3_REGION:
                    storage_options["aws_region"] = str(settings.S3_REGION)
                if settings.S3_ENDPOINT_URL:
                    storage_options["endpoint_url"] = str(settings.S3_ENDPOINT_URL)
                    storage_options["aws_endpoint"] = str(settings.S3_ENDPOINT_URL)

            conn = lancedb.connect(
                uri=uri,
                storage_options=storage_options if storage_options else None,
            )
            return LanceDBVectorStore(uri=uri, connection=conn, table_name="documents")

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
        return StorageContext.from_defaults(
            persist_dir=persist_dir,
            graph_store=graph_store
            if self.config.retrieval.graph_store == GraphStoreType.FALKORDB
            else None,
            index_store=None,
            vector_store=self._get_vector_store(),
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

    def _build_retriever(
        self,
        index: VectorStoreIndex,
        top_k: int = 5,
        language: str = "german",
        use_async: bool = True,
        nodes: list[TextNode] | None = None,
    ):
        assert isinstance(index, VectorStoreIndex), f"Expected VectorStoreIndex, got {type(index)}"
        if self.config.retrieval.backend == RetrievalBackend.HYBRID:
            bm25_kwargs = {}
            if nodes:
                bm25_kwargs["nodes"] = nodes
            elif hasattr(index, "docstore") and len(index.docstore.docs) > 0:
                bm25_kwargs["index"] = index
            else:
                try:
                    vs = getattr(index, "_vector_store", None)
                    if vs is not None:
                        tbl = getattr(vs, "_table", None)
                        if tbl is None and hasattr(vs, "_connection"):
                            tbl = vs._connection.open_table(getattr(vs, "table_name", "documents"))
                        if tbl is not None:
                            df = tbl.to_pandas()
                            loaded_nodes = []
                            for _, row in df.iterrows():
                                txt = row.get("text", "")
                                doc_id = str(row.get("id", row.get("doc_id", "")))
                                if txt:
                                    loaded_nodes.append(TextNode(text=txt, id_=doc_id))
                            if loaded_nodes:
                                bm25_kwargs["nodes"] = loaded_nodes
                except Exception as e:
                    logger.warning(f"Could not load nodes from vector store for BM25: {e}")

            if "nodes" in bm25_kwargs or "index" in bm25_kwargs:
                bm25 = BM25Retriever.from_defaults(
                    similarity_top_k=top_k,
                    language=language,
                    stemmer=Stemmer.Stemmer(
                        language if language in ("german", "english") else "german"
                    ),
                    **bm25_kwargs,
                )
                retriever = QueryFusionRetriever(
                    [
                        index.as_retriever(similarity_top_k=top_k),
                        bm25,
                    ],
                    llm=self._get_llm(),
                    num_queries=1,
                    use_async=use_async,
                    similarity_top_k=top_k,
                )
            else:
                retriever = index.as_retriever(similarity_top_k=top_k)

        elif self.config.retrieval.backend == RetrievalBackend.DENSE:
            retriever = index.as_retriever(similarity_top_k=top_k)

        else:
            raise ValueError("Invalid retrieval backend for VectorStoreIndex")

        return retriever

    def _get_response_synthesizer(self) -> BaseSynthesizer:
        kwargs = dict(llm=self._get_llm(), streaming=False)
        if (
            self.config.generation.response_synthesizer
            == ResponseSynthesizerType.COMPACT_AND_REFINE
        ):
            return CompactAndRefine(**kwargs, verbose=False)
        elif (
            self.config.generation.response_synthesizer == ResponseSynthesizerType.SIMPLE_SUMMARIZE
        ):
            return SimpleSummarize(**kwargs)
        elif self.config.generation.response_synthesizer == ResponseSynthesizerType.TREE_SUMMARIZE:
            return TreeSummarize(**kwargs, use_async=True)
        elif self.config.generation.response_synthesizer == ResponseSynthesizerType.REFINE:
            return Refine(**kwargs, verbose=False)
        else:
            raise ValueError("Invalid response synthesizer")

    @step
    async def process_start(self, ctx: Context, ev: StartEvent) -> StopEvent | None:
        """Entry point for Ingestion."""
        ingest: bool = ev.get("ingest", False)
        if ingest:
            logger.info("Ingesting documents...")
            documents = DataLoader().load_documents(self.config.data)
            index = self._build_index(documents)
            return StopEvent(result=index)
        else:
            return None

    @step
    async def retrieve(self, ctx: Context, ev: StartEvent) -> RetrieverEvent | None:
        """Entry point for RAG, triggered by a StartEvent with `query`."""
        query = ev.query
        index: PropertyGraphIndex | VectorStoreIndex = ev.index

        if not query:
            return None

        if index is None:
            return None

        logger.info(f"Query: {query}")
        await ctx.store.set("query", query)

        if isinstance(index, PropertyGraphIndex):
            kwargs = {}
            kwargs["response_mode"] = "tree_summarize"
            kwargs["embedding_mode"] = "hybrid"
            retriever = index.as_retriever(
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

        logger.info(f"Retrieved {len(nodes)} nodes")

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
                logger.info(f"Reranked {len(nodes)} nodes")
            except Exception as e:
                logger.warning(
                    f"CohereRerank failed: {e}. Falling back to top_n un-reranked nodes."
                )
                nodes = nodes[: self.config.rerank.top_n]

        return RerankEvent(nodes=nodes)

    def convert_nodes_to_results(self, nodes: list[NodeWithScore]) -> list[RetrievalResult]:
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

    @step
    async def synthesize(self, ctx: Context, ev: RerankEvent) -> StopEvent:
        """Synthesize response using CompactAndRefine."""
        query = await ctx.store.get("query")
        if query is None:
            raise ValueError("No query found. Please ingest documents first.")

        if self.config.retrieval_only:
            logger.info("Retrieval only mode enabled. Returning retrieved nodes.")
            return StopEvent(result=self.convert_nodes_to_results(ev.nodes))

        summarizer = self._get_response_synthesizer()
        response = await summarizer.asynthesize(query, nodes=ev.nodes)
        response_model = self.convert_nodes_to_results(response.source_nodes)
        return StopEvent(result=response_model)


def execute_pipeline(
    query: str,
    workflow: RAGWorkflow,
    ingest_data: bool = False,
) -> list[RetrievalResult]:
    """Execute retrieval on an existing index and return standardized candidate results synchronously."""

    async def _arun() -> list[RetrievalResult]:
        index = await workflow.run(ingest=ingest_data)
        response = await workflow.run(query=query, index=index)
        return response

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, _arun()).result()
    else:
        return asyncio.run(_arun())

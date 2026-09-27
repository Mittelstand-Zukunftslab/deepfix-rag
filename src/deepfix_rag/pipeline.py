import asyncio
import concurrent.futures
from pathlib import Path
import time
from typing import Any, Optional
import lancedb
from llama_index.core import (
    Document,
    PropertyGraphIndex,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.graph_stores.types import GraphStore, PropertyGraphStore
from llama_index.core.indices.base import BaseIndex
from llama_index.core.indices.property_graph import (
    LLMSynonymRetriever,
    SchemaLLMPathExtractor,
    VectorContextRetriever,
)
from llama_index.core.node_parser import NodeParser, get_leaf_nodes
from llama_index.core.response_synthesizers import (
    CompactAndRefine,
    Refine,
    SimpleSummarize,
    TreeSummarize,
)
from llama_index.core.response_synthesizers.base import (
    BaseSynthesizer,
)
from llama_index.core.retrievers.auto_merging_retriever import AutoMergingRetriever
from llama_index.core.retrievers.fusion_retriever import (
    FUSION_MODES,
    QueryFusionRetriever,
)
from llama_index.core.schema import BaseNode, NodeWithScore
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
from llama_index.graph_stores.falkordb import FalkorDBGraphStore, FalkorDBPropertyGraphStore
from llama_index.graph_stores.memgraph import MemgraphPropertyGraphStore
from llama_index.llms.litellm import LiteLLM
from llama_index.postprocessor.cohere_rerank import CohereRerank
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.lancedb import LanceDBVectorStore
from pydantic import BaseModel, Field
import Stemmer
from turbovec.llama_index import TurboQuantVectorStore

from deepfix_rag.config.schema import (
    GRAPH_EXTRACTION_SCHEMAS,
    ChunkingStrategy,
    GraphStoreType,
    RAGConfig,
    ResponseSynthesizerType,
    RetrievalBackend,
    VectorStoreType,
)
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.data import DataLoader, build_node_parser


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

class RAGResult(BaseModel):
    """RAG result."""
    retrieval_results: list[RetrievalResult] = Field(..., description="The retrieval results")
    generated_answer: Optional[str] = Field(None, description="The generated answer of the document")

class ChunkStatistics(BaseModel):
    """Chunking statistics captured during index build."""

    count: int = Field(..., ge=0, description="Number of chunks written to the index.")
    strategy: str = Field(..., description="Chunking strategy name.")
    size: int = Field(..., ge=0, description="Configured chunk size.")
    overlap: int = Field(..., ge=0, description="Configured chunk overlap.")


class IndexBuildMetadata(BaseModel):
    """Structured metadata returned by index builds."""

    document_count: int = Field(..., ge=0, description="Number of source documents ingested.")
    chunk_statistics: ChunkStatistics
    build_duration_seconds: float = Field(
        ..., ge=0.0, description="Wall-clock duration of the index build."
    )
    storage_bytes: int = Field(
        ..., ge=0, description="On-disk footprint of the local index directory."
    )
    paradigm: str = Field(..., description="Retrieval backend used for the build.")
    embedding_model: str = Field(..., description="Embedding model identifier.")
    reranker_model: str = Field(..., description="Configured re-ranker model identifier.")


class CustomLiteLLM(LiteLLM):
    custom_context_window: int = settings.LLM_CONTEXT_WINDOW

    @property
    def metadata(self):
        meta = super().metadata
        meta.context_window = self.custom_context_window
        return meta

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        return await asyncio.to_thread(self.complete, prompt, formatted=formatted, **kwargs)

    async def achat(self, messages: Any, **kwargs: Any):
        return await asyncio.to_thread(self.chat, messages, **kwargs)


class RAGWorkflow(Workflow):
    """Event-driven RAG Workflow supporting Ingestion, Retrieval, Reranking, and Synthesis."""

    def __init__(
        self,
        config: RAGConfig | None = None,
        llm: BaseLLM | None = None,
        embed_model: BaseEmbedding | None = None,
        timeout: float | None = 600.0,
        **kwargs: Any,
    ):
        super().__init__(timeout=timeout, **kwargs)
        self.config = config or RAGConfig()
        self.llm = llm
        self.embed_model = embed_model
        self._index: BaseIndex | None = None
        self.last_build_metadata: IndexBuildMetadata | None = None

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
                    storage_options["region"] = str(settings.S3_REGION)
                if settings.S3_ENDPOINT_URL:
                    storage_options["endpoint"] = str(settings.S3_ENDPOINT_URL)

            conn = lancedb.connect(
                uri=uri,
                storage_options=storage_options if storage_options else None,
            )
            return LanceDBVectorStore(uri=uri, connection=conn, table_name="documents")

        elif self.config.retrieval.vector_store == VectorStoreType.TURBO_QUANT:
            return TurboQuantVectorStore.from_params(bit_width=self.config.retrieval.bit_width)
        else:
            raise ValueError("Invalid vector store")

    def _normalize_memgraph_url(self, url: str) -> str:
        """Normalize configured Memgraph URLs to a neo4j-driver compatible scheme.

        Settings and the PRD expose an HTTP(S) Memgraph endpoint. LlamaIndex's
        ``MemgraphPropertyGraphStore`` uses the Neo4j/Bolt driver, which only
        accepts ``bolt`` / ``neo4j`` schemes, so map http(s) to bolt(+s).
        """
        normalized = url.strip()
        lower = normalized.lower()
        if lower.startswith("https://"):
            return "bolt+s://" + normalized[len("https://") :]
        if lower.startswith("http://"):
            return "bolt://" + normalized[len("http://") :]
        return normalized

    def _falkordb_url(self) -> str:
        """Build a Redis-compatible FalkorDB URL from host/port settings."""
        return f"redis://{settings.FALKORDB_HOST}:{settings.FALKORDB_PORT}"

    def _get_graph_store(self) -> GraphStore | PropertyGraphStore | None:
        if self.config.retrieval.graph_store == GraphStoreType.MEMGRAPH:
            return MemgraphPropertyGraphStore(
                url=self._normalize_memgraph_url(settings.MEMGRAPH_URL),
                username=settings.MEMGRAPH_USERNAME,
                password=settings.MEMGRAPH_PASSWORD,
            )
        if self.config.retrieval.graph_store == GraphStoreType.FALKORDB:
            falkor_kwargs: dict[str, Any] = {}
            if settings.FALKORDB_USERNAME:
                falkor_kwargs["username"] = settings.FALKORDB_USERNAME
            if settings.FALKORDB_PASSWORD:
                falkor_kwargs["password"] = settings.FALKORDB_PASSWORD
            return FalkorDBPropertyGraphStore(
                url=self._falkordb_url(),
                database=settings.FALKORDB_DATABASE,
                **falkor_kwargs,
            )
        return None

    def _get_storage_context(self, persist_dir=None) -> StorageContext:
        graph_store = self._get_graph_store()
        uses_property_graph = self.config.retrieval.graph_store in (
            GraphStoreType.MEMGRAPH,
            GraphStoreType.FALKORDB,
        )
        return StorageContext.from_defaults(
            persist_dir=persist_dir,
            graph_store=graph_store if isinstance(graph_store, FalkorDBGraphStore) else None,
            index_store=None,
            vector_store=self._get_vector_store(),
            property_graph_store=graph_store if uses_property_graph else None,
            image_store=None,  # images of scanned PDFs
        )

    def _index_exists(self) -> bool:
        """Check if an index already exists for the configured storage backend."""
        try:
            if self.config.retrieval.graph_store != GraphStoreType.NONE:
                return False
            if self.config.retrieval.vector_store == VectorStoreType.LANCEDB:
                uri = self.config.retrieval.lancedb_index_dir
                if not str(uri).startswith("s3://") and not Path(uri).exists():
                    return False
                vector_store = self._get_vector_store()
                conn = getattr(vector_store, "_connection", None)
                if conn is not None:
                    table_name = getattr(vector_store, "_table_name", None)
                    if table_name is None:
                        return False
                    tables_res = (
                        conn.list_tables() if hasattr(conn, "list_tables") else conn.table_names()
                    )
                    tables = getattr(tables_res, "tables", tables_res)
                    tables = list(tables) if not isinstance(tables, list) else tables
                    if table_name in tables:
                        tbl = conn.open_table(table_name)
                        count = tbl.count_rows() if hasattr(tbl, "count_rows") else len(tbl)
                        return count > 0
            return False
        except Exception as e:
            LOGGER.debug(f"Index existence check failed: {e}")
            return False

    def _load_existing_index(self) -> BaseIndex:
        """Load an existing index from the storage backend."""
        if (
            self.config.retrieval.graph_store != GraphStoreType.NONE
            or self.config.retrieval.backend in (RetrievalBackend.GRAPH, RetrievalBackend.MIXED)
        ):
            storage_context = self._get_storage_context()
            graph_store = getattr(
                storage_context,
                "property_graph_store",
                getattr(storage_context, "graph_store", None),
            )
            return PropertyGraphIndex.from_existing(
                property_graph_store=graph_store,
                embed_model=self._get_embed_model(),
                llm=self._get_llm(),
            )
        else:
            vector_store = self._get_vector_store()
            return VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self._get_embed_model(),
            )

    def _get_node_parser(self) -> NodeParser:
        """Build a node parser from the configured chunking strategy."""
        embed_model = None
        if self.config.chunking.strategy == ChunkingStrategy.SEMANTIC:
            embed_model = self._get_embed_model()
        return build_node_parser(self.config.chunking, embed_model=embed_model)

    def _build_index(self, documents: list[Document], storage_context=None, **kwargs) -> BaseIndex:
        uses_graph = (
            self.config.retrieval.graph_store != GraphStoreType.NONE
            or self.config.retrieval.backend in (RetrievalBackend.GRAPH, RetrievalBackend.MIXED)
        )
        if self.config.chunking.strategy == ChunkingStrategy.HIERARCHICAL and uses_graph:
            raise ValueError(
                "Hierarchical chunking is not supported with graph or mixed retrieval backends."
            )

        if storage_context is None:
            storage_context = self._get_storage_context()

        if self.config.chunking.strategy == ChunkingStrategy.HIERARCHICAL:
            parser = self._get_node_parser()
            all_nodes = parser.get_nodes_from_documents(documents)
            leaf_nodes = get_leaf_nodes(all_nodes)
            storage_context.docstore.add_documents(all_nodes)
            return VectorStoreIndex(
                leaf_nodes,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
            )

        transformations = [self._get_node_parser()]

        if uses_graph:
            schema_key = str(self.config.retrieval.graph_extraction_schema).lower().strip()
            schema_info = GRAPH_EXTRACTION_SCHEMAS.get(schema_key)
            extractor_kwargs: dict[str, Any] = {
                "llm": self._get_llm(),
                "max_triplets_per_chunk": self.config.retrieval.graph_max_paths_per_chunk,
            }
            if schema_info:
                extractor_kwargs["possible_entities"] = schema_info.entities
                extractor_kwargs["possible_relations"] = schema_info.relations

            index = PropertyGraphIndex.from_documents(
                documents,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
                kg_extractors=[SchemaLLMPathExtractor(**extractor_kwargs)],
                transformations=transformations,
                show_progress=True,
            )
        else:
            index = VectorStoreIndex.from_documents(
                documents,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
                transformations=transformations,
            )
            if hasattr(index, "docstore") and len(index.docstore.docs) == 0:
                index.docstore.add_documents(documents)
        return index

    def _get_dense_retriever(self, index: BaseIndex, top_k: int) -> BaseRetriever:
        """Construct a dense vector retriever from VectorStoreIndex or PropertyGraphIndex."""
        if isinstance(index, PropertyGraphIndex):
            vector_store = index.vector_store or self._get_vector_store()
            return VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self._get_embed_model(),
            ).as_retriever(similarity_top_k=top_k)
        elif isinstance(index, VectorStoreIndex):
            return index.as_retriever(similarity_top_k=top_k)
        else:
            raise ValueError(f"Unsupported index type for dense retriever: {type(index)}")

    def _load_nodes_from_vector_store(self) -> list[BaseNode]:
        """Load node text from the configured LanceDB vector store for BM25 rebuild."""
        vector_store = self._get_vector_store()
        if not hasattr(vector_store, "get_nodes"):
            return []
        try:
            nodes = vector_store.get_nodes()
            return list(nodes) if nodes else []
        except Exception as e:
            LOGGER.debug(f"Failed to load nodes from vector store for BM25: {e}")
            return []

    def _get_bm25_retriever(
        self, index: BaseIndex, top_k: int, language: str = "german"
    ) -> BaseRetriever:
        """Construct a BM25 sparse lexical retriever."""
        bm25_kwargs: dict[str, Any] = {}
        if hasattr(index, "docstore") and len(index.docstore.docs) > 0:
            bm25_kwargs["index"] = index
        elif hasattr(index, "nodes") and len(index.nodes) > 0:
            bm25_kwargs["nodes"] = index.nodes
        else:
            # Persisted-only indexes (e.g. VectorStoreIndex.from_vector_store) have an empty
            # docstore; recover node text from the vector store before giving up on BM25.
            nodes = self._load_nodes_from_vector_store()
            if not nodes:
                if hasattr(index, "docstore"):
                    LOGGER.warning(
                        "BM25Retriever docstore has 0 documents; falling back to dense retriever."
                    )
                    return self._get_dense_retriever(index, top_k)
                raise ValueError("No index or docstore found for BM25Retriever")
            bm25_kwargs["nodes"] = nodes

        bm25 = BM25Retriever.from_defaults(
            similarity_top_k=top_k,
            language=language,
            stemmer=Stemmer.Stemmer(language),
            **bm25_kwargs,
        )
        if hasattr(bm25, "bm25") and bm25.bm25 is not None:
            bm25.bm25.k1 = self.config.retrieval.bm25_k1
            bm25.bm25.b = self.config.retrieval.bm25_b
        return bm25

    def _get_graph_retriever(self, index: BaseIndex, top_k: int) -> BaseRetriever:
        """Construct a PropertyGraph retriever with VectorContextRetriever and LLMSynonymRetriever traversal."""

        def get_subretrievers(graph_store: PropertyGraphStore) -> list[BaseRetriever]:
            return [
                LLMSynonymRetriever(
                    graph_store=graph_store,
                    include_text=True,
                    llm=self._get_llm(),
                    path_depth=self.config.retrieval.graph_traversal_hops,
                ),
                VectorContextRetriever(
                    graph_store=graph_store,
                    embed_model=self._get_embed_model(),
                    similarity_top_k=top_k,
                    path_depth=self.config.retrieval.graph_traversal_hops,
                    include_text=True,
                ),
            ]
        
        if isinstance(index, PropertyGraphIndex):
            graph_store = getattr(
                index,
                "property_graph_store",
                getattr(index, "_property_graph_store", None),
            )            
            return index.as_retriever(
                sub_retrievers=get_subretrievers(graph_store),
                similarity_top_k=top_k,
            )
        else:
            graph_store = self._get_graph_store()
            if graph_store is not None:
                pg_index = PropertyGraphIndex.from_existing(
                    property_graph_store=graph_store,
                    embed_model=self._get_embed_model(),
                )
                return pg_index.as_retriever(
                    sub_retrievers=get_subretrievers(graph_store),
                    similarity_top_k=top_k,
                )
            raise ValueError(
                f"PropertyGraphIndex or active graph store required for Graph retrieval, got {type(index)}"
            )

    def _build_retriever(
        self,
        index: BaseIndex,
        top_k: int = 5,
        language: str = "german",
        use_async: bool = True,
    ) -> BaseRetriever:
        backend = self.config.retrieval.backend

        if backend == RetrievalBackend.DENSE:
            retriever: BaseRetriever = self._get_dense_retriever(index, top_k)

        elif backend == RetrievalBackend.BM25:
            retriever = self._get_bm25_retriever(index, top_k, language)

        elif backend == RetrievalBackend.GRAPH:
            retriever = self._get_graph_retriever(index, top_k)

        elif backend == RetrievalBackend.HYBRID:
            dense_retriever = self._get_dense_retriever(index, top_k)
            bm25_retriever = self._get_bm25_retriever(index, top_k, language)
            fusion_mode_enum = FUSION_MODES(self.config.retrieval.fusion_mode.value)

            retriever = QueryFusionRetriever(
                [dense_retriever, bm25_retriever],
                llm=self._get_llm(),
                retriever_weights=[
                    self.config.retrieval.dense_weight,
                    self.config.retrieval.bm25_weight,
                ],
                mode=fusion_mode_enum,
                num_queries=self.config.retrieval.query_expansion_num,
                use_async=use_async,
                similarity_top_k=top_k,
                query_gen_prompt=self.config.retrieval.query_gen_prompt,
            )

        elif backend == RetrievalBackend.MIXED:
            dense_retriever = self._get_dense_retriever(index, top_k)
            bm25_retriever = self._get_bm25_retriever(index, top_k, language)
            graph_retriever = self._get_graph_retriever(index, top_k)
            fusion_mode_enum = FUSION_MODES(self.config.retrieval.fusion_mode.value)

            w_dense = self.config.retrieval.dense_weight
            w_bm25 = self.config.retrieval.bm25_weight
            w_graph = self.config.retrieval.graph_weight

            total_weight = w_dense + w_bm25 + w_graph
            if total_weight <= 0:
                raise ValueError(
                    "Sum of retrieval weights (dense_weight, bm25_weight, graph_weight) must be strictly positive."
                )
            if not (0.999 <= total_weight <= 1.001):
                w_dense = round(w_dense / total_weight, 4)
                w_bm25 = round(w_bm25 / total_weight, 4)
                w_graph = round(1.0 - w_dense - w_bm25, 4)

            retriever = QueryFusionRetriever(
                [dense_retriever, bm25_retriever, graph_retriever],
                llm=self._get_llm(),
                retriever_weights=[w_dense, w_bm25, w_graph],
                mode=fusion_mode_enum,
                num_queries=self.config.retrieval.query_expansion_num,
                use_async=use_async,
                similarity_top_k=top_k,
                query_gen_prompt=self.config.retrieval.query_gen_prompt
            )

        else:
            raise ValueError(f"Invalid retrieval backend: {backend}")

        if self.config.chunking.strategy == ChunkingStrategy.HIERARCHICAL and isinstance(
            index, VectorStoreIndex
        ):
            retriever = AutoMergingRetriever(retriever, storage_context=index.storage_context)
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

    def _count_index_chunks(self, index: BaseIndex) -> int:
        """Count chunks stored in the built index."""
        if hasattr(index, "docstore") and len(index.docstore.docs) > 0:
            return len(index.docstore.docs)
        if hasattr(index, "nodes") and index.nodes is not None:
            return len(index.nodes)
        nodes = self._load_nodes_from_vector_store()
        if nodes:
            return len(nodes)
        try:
            if self.config.retrieval.vector_store == VectorStoreType.LANCEDB:
                vector_store = self._get_vector_store()
                conn = getattr(vector_store, "_connection", None)
                table_name = getattr(vector_store, "_table_name", None)
                if conn is not None and table_name is not None:
                    tables_res = (
                        conn.list_tables() if hasattr(conn, "list_tables") else conn.table_names()
                    )
                    tables = getattr(tables_res, "tables", tables_res)
                    tables = list(tables) if not isinstance(tables, list) else tables
                    if table_name in tables:
                        tbl = conn.open_table(table_name)
                        return int(tbl.count_rows() if hasattr(tbl, "count_rows") else len(tbl))
        except Exception as e:
            LOGGER.debug(f"Failed to count LanceDB rows for chunk statistics: {e}")
        return 0

    def _compute_storage_bytes(self) -> int:
        """Sum on-disk bytes for a local LanceDB index directory."""
        uri = self.config.retrieval.lancedb_index_dir
        if str(uri).startswith("s3://"):
            return 0
        path = Path(uri)
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def _record_build_metadata(
        self,
        *,
        document_count: int,
        chunk_count: int,
        build_duration_seconds: float,
    ) -> IndexBuildMetadata:
        """Persist and return index build metadata for CLI consumers."""
        backend = self.config.retrieval.backend
        paradigm = backend.value if hasattr(backend, "value") else str(backend)
        metadata = IndexBuildMetadata(
            document_count=document_count,
            chunk_statistics=ChunkStatistics(
                count=chunk_count,
                strategy=self.config.chunking.strategy,
                size=self.config.chunking.size,
                overlap=self.config.chunking.overlap,
            ),
            build_duration_seconds=build_duration_seconds,
            storage_bytes=self._compute_storage_bytes(),
            paradigm=paradigm,
            embedding_model=settings.EMBEDDING_MODEL,
            reranker_model=settings.COHERE_MODEL,
        )
        self.last_build_metadata = metadata
        return metadata

    @step
    async def ingestion(self, ctx: Context, ev: StartEvent) -> StopEvent | None:
        """Entry point for Ingestion."""
        ingest: bool = ev.get("ingest", False)
        if ingest:
            started = time.perf_counter()
            document_count = 0
            if self.config.retrieval.load_if_exists and self._index_exists():
                LOGGER.info("Existing index detected. Loading pre-built index...")
                index = self._load_existing_index()
                document_count = self._count_index_chunks(index)
            else:
                LOGGER.info("Ingesting corpus documents...")
                documents = DataLoader(self.config.data).load_corpus()
                document_count = len(documents)
                # PropertyGraph extraction and some LLM clients call asyncio.run()
                index = await asyncio.to_thread(self._build_index, documents)
            self._index = index
            self._record_build_metadata(
                document_count=document_count,
                chunk_count=self._count_index_chunks(index),
                build_duration_seconds=time.perf_counter() - started,
            )
            return StopEvent(result=index)
        else:
            return None

    @step
    async def retrieve(self, ctx: Context, ev: StartEvent) -> RetrieverEvent | None:
        """Entry point for RAG, triggered by a StartEvent with `query`."""
        query = ev.get("query", None)
        index: BaseIndex | None = ev.get("index", self._index)

        if not query:
            return None

        if index is None:
            return None

        LOGGER.debug(f"Query: {query}")
        await ctx.store.set("query", query)

        retriever = self._build_retriever(
            index=index,
            top_k=self.config.retrieval.top_k,
            language=self.config.retrieval.language,
            use_async=True,
        )
        nodes = await retriever.aretrieve(query)

        LOGGER.debug(f"Retrieved {len(nodes)} nodes")

        return RetrieverEvent(nodes=nodes)

    @step
    async def rerank(self, ctx: Context, ev: RetrieverEvent) -> RerankEvent:
        """Rerank candidate nodes using CohereRerank with custom base_url."""
        query = await ctx.store.get("query")
        nodes = ev.nodes
        reranker = self._get_reranker()

        if reranker is None:
            LOGGER.debug("No reranker configured")
            return RerankEvent(nodes=nodes)

        nodes = reranker.postprocess_nodes(nodes, query_str=query)
        LOGGER.debug(f"Reranked {len(nodes)} nodes")

        return RerankEvent(nodes=nodes)

    def convert_nodes_to_results(self, nodes: list[NodeWithScore]) -> list[RetrievalResult]:
        """Convert retrieved nodes to results keyed by parent `doc_id`, never `chunk_id`."""
        results: list[RetrievalResult] = []
        for node in nodes:
            inner = node.node
            metadata: dict = inner.metadata or {}
            raw_id = metadata.get("doc_id", inner.node_id)
            results.append(
                RetrievalResult(
                    doc_id=DataLoader.parent_doc_id(str(raw_id)),
                    score=node.score,
                    text=inner.get_content(),
                )
            )
        return results

    @step
    async def synthesize(self, ctx: Context, ev: RerankEvent) -> StopEvent:
        """Synthesize response using CompactAndRefine."""
        query = await ctx.store.get("query")
        if query is None:
            raise ValueError("Provide 'query=<your-query>'.")

        if self.config.retrieval_only:
            LOGGER.info("Retrieval only mode enabled. Returning retrieved nodes.")
            return StopEvent(result=RAGResult(retrieval_results=self.convert_nodes_to_results(ev.nodes), generated_answer=None))

        summarizer = self._get_response_synthesizer()
        response = await summarizer.asynthesize(query, nodes=ev.nodes)
        generated_answer=str(response.response) if response else None
        retrieval_results = self.convert_nodes_to_results(ev.nodes)
        response_model = RAGResult(retrieval_results=retrieval_results, generated_answer=generated_answer)
        return StopEvent(result=response_model)


async def run_workflow_server(
    config: RAGConfig | None = None,
    llm: BaseLLM | None = None,
    embed_model: BaseEmbedding | None = None,
    timeout: float | None = 600.0, 
    host:str='0.0.0.0',
    port:int=8090
    ):"""Get a workflow server instance."""
    from llama_agents.server import WorkflowServer
    from dbos import DBOS
    from llama_agents.dbos import DBOSRuntime
    from llama_agents.server import WorkflowServer

    DBOS(config={"name": "RAG-workflow", "run_admin_server": False})
    runtime = DBOSRuntime()
    workflow = RAGWorkflow(config=config, llm=llm, embed_model=embed_model, timeout=timeout,runtime=runtime)

    server= WorkflowServer(workflow_store=runtime.create_workflow_store(),runtime=runtime.build_server_runtime())
    server.add_workflow("RAG-workflow",workflow)

    await server.start()
    
    await server.serve(host=host, port=port)


def execute_pipeline(
    query: str,
    workflow: RAGWorkflow,
    ingest_data: bool = False,
) -> RAGResult:
    """Execute retrieval on an existing index and return standardized candidate results synchronously."""

    async def _arun() -> RAGResult:
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

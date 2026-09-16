"""Pydantic schema for RAG configuration validation."""

from enum import StrEnum

from pydantic import BaseModel, Field
from .settings import settings

class RetrievalBackend(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    GRAPH = "graph"
    # VECTORLESS = "vectorless"


class GenerationMetricType(StrEnum):
    FAITHFULNESS = "faithfulness"
    CONTEXT_PRECISION = "context_precision"
    CONTEXT_RECALL = "context_recall"
    ANSWER_RELEVANCY = "answer_relevancy"
    CONTEXTUAL_RELEVANCY = "contextual_relevancy"


class RetrievalMetricType(StrEnum):
    NDCG = "ndcg"
    RECALL = "recall"


class VectorStoreType(StrEnum):
    LANCEDB = "lancedb"
    TURBO_QUANT = "turbo-quant"


class GraphStoreType(StrEnum):
    MEMGRAPH = "memgraph"
    FALKORDB = "falkordb"
    NONE = "none"

class DataPreset(StrEnum):
    BLEQA = "clips/bLLeQA"
    LEGAL_RAG_BENCH = "isaacus/legal-rag-bench"
    BUNDESTAG_QA = "wirthual/dip-bundestag-qa"
    BUNDESTAG_CORPUS = "wirthual/dip-bundestag"

class DataSourceType(StrEnum):
    HUGGINGFACE = "huggingface"
    LOCAL_FILE = "local_file"
    DIRECTORY = "directory"

class ResponseSynthesizerType(StrEnum):
    COMPACT_AND_REFINE = "compact_and_refine"
    SIMPLE_SUMMARIZE = "simple_summarize"
    TREE_SUMMARIZE = "tree_summarize"
    REFINE = "refine"

class DataLoaderConfig(BaseModel):
    """Pydantic model to strictly validate data loading requests."""

    source_type: DataSourceType = DataSourceType.HUGGINGFACE
    path_or_repo: str = Field(default=DataPreset.BUNDESTAG_QA, description="Local path or HuggingFace repo ID.")
    split: str | None = Field("train", description="Dataset split (HF only).")
    text_column: str = Field("text", description="Which column to map to the Document body.")
    doc_id_column: str = Field("id", description="Which column to map to the Document ID.")
    query_column: str = Field("question", description="Which column to map to the Question/Query.")
    answer_column: str = Field(
        "answer", description="Which column to map to the Ground Truth/Answer."
    )
    config_name: str | None = Field(
        None, description="Dataset config or subset name (e.g. 'qa' for HF)."
    )


class ChunkingConfig(BaseModel):
    """Document chunking hyperparameters."""

    strategy: str = Field(
        default="recursive", description="Chunking strategy (recursive, fixed, semantic, etc.)."
    )
    size: int = Field(default=512, ge=64, le=4096, description="Chunk token or character size.")
    overlap: int = Field(default=64, ge=0, description="Chunk overlap size.")


class EmbeddingConfig(BaseModel):
    """Embedding model and endpoint settings."""

    batch_size: int = Field(default=32, ge=1, description="Embedding batch size.")


class RetrievalConfig(BaseModel):
    """Retrieval parameters."""

    backend: RetrievalBackend = Field(
        default=RetrievalBackend.HYBRID,
        description="Retrieval backend (dense, hybrid, graph, vectorless).",
    )
    vector_store: VectorStoreType = Field(
        default=VectorStoreType.LANCEDB, description="Vector store type."
    )
    graph_store: GraphStoreType = Field(
        default=GraphStoreType.NONE, description="Graph store type."
    )
    lancedb_table_name: str = Field(
        default="documents", description="Table name for LanceDB."
    )
    lancedb_index_dir: str = Field(
        default=settings.S3_URI, description="Directory for LanceDB index."
    )
    top_k: int = Field(
        default=10, ge=1, le=100, description="Number of retrieved documents before re-ranking."
    )
    hybrid_alpha: float | None = Field(
        default=0.5, ge=0.0, le=1.0, description="Dense vs lexical weight for hybrid search."
    )
    bit_width: int = Field(default=4, ge=2, le=4, description="Bit width for quantization.")
    language: str = Field(default="german", description="Language of the documents.")


class EvalConfig(BaseModel):
    """Evaluation hyperparameters."""

    retrieval_metrics: list[RetrievalMetricType] = Field(
        default_factory=lambda: [RetrievalMetricType.NDCG, RetrievalMetricType.RECALL],
        description="Retrieval metrics.",
    )
    generation_metrics: list[GenerationMetricType] = Field(
        default_factory=lambda: [
            GenerationMetricType.FAITHFULNESS,
            GenerationMetricType.CONTEXT_PRECISION,
            GenerationMetricType.CONTEXT_RECALL,
            GenerationMetricType.ANSWER_RELEVANCY,
            GenerationMetricType.CONTEXTUAL_RELEVANCY,
        ],
        description="Generation metrics.",
    )

    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Metrics threshold.")
    concurrency: int = Field(
        default=5, ge=1, le=50, description="Max parallel QA cases during evaluation."
    )


class RerankConfig(BaseModel):
    """Re-ranking stage settings."""

    enabled: bool = Field(default=False, description="Whether re-ranking stage is active.")
    top_n: int = Field(
        default=5, ge=1, description="Final number of top passages passed to generation."
    )


class GenerationConfig(BaseModel):
    """LLM generation and prompting settings."""

    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: int = Field(default=4096, ge=64, description="Max generated tokens.")
    system_prompt: str | None = Field(default=None, description="Custom system prompt.")
    response_synthesizer: ResponseSynthesizerType = Field(
        default=ResponseSynthesizerType.SIMPLE_SUMMARIZE, description="Response synthesizer type."
    )
    context_window: int = Field(default=20000, ge=64, description="Context window size.")

class RAGConfig(BaseModel):
    """Top-level RAG Pipeline Configuration Schema."""

    version: str = Field(default="1.0", description="Configuration schema version.")
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    data: DataLoaderConfig = Field(default_factory=DataLoaderConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    retrieval_only: bool = Field(default=False, description="Whether retrieval only is active.")

"""Pydantic schema for RAG configuration validation."""

from botocore import retries
from typing import Literal

from pydantic import BaseModel, Field
from enum import StrEnum

class RetrievalBackend(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    GRAPH = "graph"
    #VECTORLESS = "vectorless"

class GenerationMetrics(StrEnum):
    FAITHFULNESS = "faithfulness"
    CONTEXT_PRECISION = "context_precision"

class RetrievalMetrics(StrEnum):
    NDCG = "ndcg"
    RECALL = "recall"

class VectorStore(StrEnum):
    LANCEDB = "lancedb"
    TURBO_QUANT = "turbo-quant"

class GraphStore(StrEnum):
    MEMGRAPH = "memgraph"
    FALKORDB = "falkordb"
    NONE = "none"

class DataConfig(BaseModel):
    """Data loading hyperparameters."""
    dataset_name: str = Field(default="msmarco", description="Name of the dataset to use.")
    
    dirname: str | None = Field(default=None, description="Directory containing the documents.")
    data_file: str | None = Field(default=None, description="Path to the data file.")
    

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
        description="Retrieval backend (pyterrier, bm25, dense, hybrid, graph, vectorless).",
    )
    vector_store: VectorStore = Field(
        default=VectorStore.LANCEDB, description="Vector store type."
    )
    graph_store: GraphStore = Field(
        default=GraphStore.NONE, description="Graph store type."
    )
    top_k: int = Field(
        default=10, ge=1, le=100, description="Number of retrieved documents before re-ranking."
    )
    hybrid_alpha: float | None = Field(
        default=0.5, ge=0.0, le=1.0, description="Dense vs lexical weight for hybrid search."
    )
    bit_width: int = Field(
        default=4, ge=2, le=4, description="Bit width for quantization."
    )

class EvalConfig(BaseModel):
    """Evaluation hyperparameters."""
    retrieval_metrics: list[RetrievalMetrics] = Field(default_factory=lambda: [RetrievalMetrics.NDCG, RetrievalMetrics.RECALL], description="Retrieval metrics.")
    generation_metrics: list[GenerationMetrics] = Field(default_factory=lambda: [GenerationMetrics.FAITHFULNESS, GenerationMetrics.CONTEXT_PRECISION], description="Generation metrics.")
    

class RerankConfig(BaseModel):
    """Re-ranking stage settings."""

    enabled: bool = Field(default=False, description="Whether re-ranking stage is active.")
    top_n: int = Field(
        default=5, ge=1, description="Final number of top passages passed to generation."
    )


class GenerationConfig(BaseModel):
    """LLM generation and prompting settings."""

    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: int = Field(default=1024, ge=64, description="Max generated tokens.")
    system_prompt: str | None = Field(default=None, description="Custom system prompt.")


class RAGConfig(BaseModel):
    """Top-level RAG Pipeline Configuration Schema."""

    version: str = Field(default="1.0", description="Configuration schema version.")
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    retrieval_only: bool = Field(default=False, description="Whether retrieval only is active.")

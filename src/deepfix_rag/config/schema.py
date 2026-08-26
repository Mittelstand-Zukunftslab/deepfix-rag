"""Pydantic schema for RAG configuration validation."""

from pydantic import BaseModel, Field


class ChunkingConfig(BaseModel):
    """Document chunking hyperparameters."""

    strategy: str = Field(
        default="recursive", description="Chunking strategy (recursive, fixed, semantic, etc.)."
    )
    size: int = Field(default=512, ge=64, le=4096, description="Chunk token or character size.")
    overlap: int = Field(default=64, ge=0, description="Chunk overlap size.")


class EmbeddingConfig(BaseModel):
    """Embedding model and endpoint settings."""

    model: str = Field(
        default="octen-embedding-8b", description="Embedding model name or identifier (e.g. octen-embedding-8b, Qwen/Qwen3-Embedding-4B)."
    )
    provider: str = Field(default="aisc", description="Inference provider: 'aisc', 'octen', 'hpi', or 'openrouter'.")
    batch_size: int = Field(default=32, ge=1, description="Embedding batch size.")


class RetrievalConfig(BaseModel):
    """Retrieval parameters."""

    backend: str = Field(
        default="pyterrier", description="Retrieval backend (pyterrier, bm25, dense, hybrid)."
    )
    top_k: int = Field(
        default=10, ge=1, le=100, description="Number of retrieved documents before re-ranking."
    )
    hybrid_alpha: float | None = Field(
        default=0.5, ge=0.0, le=1.0, description="Dense vs lexical weight for hybrid search."
    )


class RerankConfig(BaseModel):
    """Re-ranking stage settings."""

    enabled: bool = Field(default=False, description="Whether re-ranking stage is active.")
    model: str | None = Field(
        default=None, description="Re-ranker model name (e.g. bge-reranker-large)."
    )
    top_n: int = Field(
        default=5, ge=1, description="Final number of top passages passed to generation."
    )


class GenerationConfig(BaseModel):
    """LLM generation and prompting settings."""

    model: str = Field(
        default="Qwen/Qwen2.5-32B-Instruct", description="Generator LLM identifier."
    )
    provider: str = Field(default="hpi", description="Inference provider: 'hpi' or 'openrouter'.")
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

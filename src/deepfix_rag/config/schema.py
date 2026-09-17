"""Pydantic schema for RAG configuration validation."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

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


class DataSourceType(StrEnum):
    HUGGINGFACE = "huggingface"
    LOCAL_FILE = "local_file"
    DIRECTORY = "directory"


class DataPreset(StrEnum):
    LEGAL_RAG_BENCH = "HF:isaacus/legal-rag-bench"
    BUNDESTAG_QA = "HF:wirthual/dip-bundestag-qa"
    BUNDESTAG_CORPUS = "HF:wirthual/dip-bundestag"

    def get_type_and_repo(self) -> tuple[DataSourceType, str]:
        if self.startswith("HF:"):
            return DataSourceType.HUGGINGFACE, self[3:]
        elif Path(self).is_file():
            return DataSourceType.LOCAL_FILE, str(self)
        elif Path(self).is_dir():
            return DataSourceType.DIRECTORY, str(self)
        else:
            raise ValueError(f"Invalid data source: {self}")

    def is_corpus_only(self) -> bool:
        return self == DataPreset.BUNDESTAG_CORPUS

    def default_corpus_subset(self) -> str | None:
        if self == DataPreset.LEGAL_RAG_BENCH:
            return "corpus"
        return None

    def default_qa_subset(self) -> str | None:
        if self == DataPreset.LEGAL_RAG_BENCH:
            return "qa"
        return None

    @classmethod
    def find_preset(cls, value_or_name: str) -> "DataPreset | None":
        """Find matching DataPreset member if exists."""
        normalized = value_or_name.upper().replace("-", "_")
        if normalized in cls.__members__:
            return cls[normalized]
        for member in cls:
            if member.value == value_or_name or member.value.removeprefix("HF:") == value_or_name:
                return member
        return None

    @classmethod
    def resolve(cls, value_or_name: str) -> tuple[DataSourceType, str]:
        """Resolve a preset name, alias, or raw path/repo into (DataSourceType, target_str)."""
        preset = cls.find_preset(value_or_name)
        if preset is not None:
            return preset.get_type_and_repo()
        if value_or_name.startswith("HF:"):
            return DataSourceType.HUGGINGFACE, value_or_name[3:]
        p = Path(value_or_name)
        if p.is_file():
            return DataSourceType.LOCAL_FILE, str(p)
        if p.is_dir():
            return DataSourceType.DIRECTORY, str(p)
        return DataSourceType.HUGGINGFACE, value_or_name


class ResponseSynthesizerType(StrEnum):
    COMPACT_AND_REFINE = "compact_and_refine"
    SIMPLE_SUMMARIZE = "simple_summarize"
    TREE_SUMMARIZE = "tree_summarize"
    REFINE = "refine"


class DataLoaderConfig(BaseModel):
    """Pydantic model to strictly validate data loading requests."""

    path_or_repo: str = Field(
        default=DataPreset.BUNDESTAG_CORPUS,
        description="Local path, dataset preset, or HuggingFace repo ID.",
    )
    split: str | None = Field("train", description="Dataset split (HF only).")
    limit: int | None = Field(None, description="Optional maximum number of records to load.")
    source_type: DataSourceType | None = Field(
        default=None, description="Inferred automatically from path_or_repo if not provided."
    )

    @model_validator(mode="after")
    def infer_source_type(self) -> "DataLoaderConfig":
        if self.source_type is None:
            st, _ = DataPreset.resolve(self.path_or_repo)
            self.source_type = st
        return self


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
    lancedb_table_name: str = Field(default="documents", description="Table name for LanceDB.")
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
    load_if_exists: bool = Field(
        default=True,
        description="Load existing index if present rather than re-ingesting corpus.",
    )


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

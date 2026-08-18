"""RAG retrieval execution wrapper."""

from deepfix_rag.config.schema import RAGConfig
from pydantic import BaseModel, Field


class RetrievalResult(BaseModel):
    """Retrieved document candidate."""
    doc_id: str = Field(..., description="Unique identifier for the document")
    score: float = Field(..., description="Retrieval score")
    text: str = Field(..., description="The text of the document")


def execute_retrieval(
    query: str, config: RAGConfig, index_dir: str | None = None
) -> list[RetrievalResult]:
    """Execute hybrid/dense/lexical retrieval pipeline for a query."""
    raise NotImplementedError("Not implemented yet")

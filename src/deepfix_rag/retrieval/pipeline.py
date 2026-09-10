"""RAG retrieval execution wrapper transitioning to LlamaIndex standard engines."""

import os
from pathlib import Path
from deepfix_rag.config.settings import settings

from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core import VectorStoreIndex, QueryBundle
from llama_index.vector_stores.lancedb import LanceDBVectorStore

from pydantic import BaseModel, Field

from deepfix_rag.config.schema import UNIMPLEMENTED_BACKENDS, RAGConfig
from deepfix_rag.retrieval.models import AISCEmbedding, OpenRouterReranker




class RetrievalResult(BaseModel):
    """Retrieved document candidate."""
    doc_id: str = Field(..., description="Unique identifier for the document")
    score: float = Field(..., description="Retrieval score")
    text: str = Field(..., description="The text of the document")


def execute_retrieval(
    query: str, config: RAGConfig, index_dir: str | None = None
) -> list[RetrievalResult]:
    """Execute retrieval using LlamaIndex VectorStore engine over LanceDB."""
    if not index_dir:
        raise ValueError("index_dir is required for retrieval.")

    if config.retrieval.backend in UNIMPLEMENTED_BACKENDS:
        raise NotImplementedError(
            f"Retrieval backend '{config.retrieval.backend}' is declared but not "
            f"yet implemented natively in LlamaIndex execute_retrieval."
        )

    # 1. Access existing Lance index via standard LlamaIndex connector
    vector_store = LanceDBVectorStore(uri=index_dir, table_name="documents")
    embed_model = AISCEmbedding(model_name=config.embedding.model, provider=config.embedding.provider)
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)

    # 2. Vector Search Retrieval Step
    retriever = VectorIndexRetriever(
        index=index, 
        similarity_top_k=config.retrieval.top_k
    )
    
    # Run Retrieval
    initial_nodes = retriever.retrieve(query)

    # 3. Node Post-Processing (Reranking)
    if initial_nodes and config.rerank.enabled and getattr(config.rerank, "model", None):
        reranker = OpenRouterReranker(model=config.rerank.model, top_n=config.rerank.top_n)
        initial_nodes = reranker.postprocess_nodes(initial_nodes, query_bundle=QueryBundle(query))

    # 4. Standardize output for CLI / MLFlow runner
    results = []
    for node in initial_nodes:
        doc_id_val = node.node.metadata.get("doc_id", node.node.node_id)
        results.append(RetrievalResult(
            doc_id=doc_id_val,
            score=node.score or 0.0,
            text=node.node.get_content()
        ))
        
    return results

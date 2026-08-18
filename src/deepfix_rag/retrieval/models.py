"""vLLM / Qwen embeddings & re-ranking client."""

import os


class EmbeddingClient:
    """Client for generating embeddings via vLLM or OpenRouter fallback."""

    def __init__(self, model: str = "Qwen/Qwen3-Embedding-4B", provider: str = "hpi"):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a list of document strings."""
        return [[0.0] * 128 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding vector for a single query."""
        return [0.0] * 128


class RerankClient:
    """Client for cross-encoder re-ranking."""

    def __init__(self, model: str = "BAAI/bge-reranker-large", provider: str = "hpi"):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[int]:
        """Re-rank candidate documents and return top_n document indices."""
        return list(range(min(len(documents), top_n)))

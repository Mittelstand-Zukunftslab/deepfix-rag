"""vLLM / Qwen embeddings, re-ranking, and generation client — via LiteLLM proxy."""

import os

import litellm


def _proxy_base_and_key() -> tuple[str, str]:
    return os.environ["LITELLM_URL"], os.environ["LITELLM_KEY"]


class EmbeddingClient:
    """Client for generating embeddings via the LiteLLM proxy (Qwen3 embedding models)."""

    def __init__(self, model: str = "qwen/qwen3-embedding-4b", provider: str = "hpi"):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        self.api_base, self.api_key = _proxy_base_and_key()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a list of document strings."""
        if not texts:
            return []
        resp = litellm.embedding(
            model=f"openai/{self.model}",
            input=texts,
            api_base=self.api_base,
            api_key=self.api_key,
            custom_llm_provider="openai",
            timeout=90,
        )
        return [item["embedding"] for item in resp["data"]]

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding vector for a single query."""
        return self.embed_documents([text])[0]


class RerankClient:
    """Client for cross-encoder re-ranking via the LiteLLM proxy."""

    def __init__(self, model: str = "rerank-english-v3.0", provider: str = "hpi"):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        self.api_base, self.api_key = _proxy_base_and_key()

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[int]:
        """Re-rank candidate documents and return top_n document indices."""
        if not documents:
            return []
        resp = litellm.rerank(
            model=f"openai/{self.model}",
            query=query,
            documents=documents,
            top_n=min(top_n, len(documents)),
            api_base=self.api_base,
            api_key=self.api_key,
            custom_llm_provider="openai",
            timeout=90,
        )
        results = resp["results"] if isinstance(resp, dict) else resp.results
        return [r["index"] if isinstance(r, dict) else r.index for r in results]


class GenerationClient:
    """Client for answer generation via the LiteLLM proxy."""

    def __init__(self, model: str, provider: str = "hpi"):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        self.api_base, self.api_key = _proxy_base_and_key()

    def _build_prompt(self, query: str, context: list[str]) -> str:
        context_text = "\n\n".join(context)
        return (
            "Answer the question using only the provided context. "
            "If the context does not contain the answer, say so explicitly.\n\n"
            f"Context:\n{context_text}\n\nQuestion: {query}\nAnswer:"
        )

    def generate(
        self, query: str, context: list[str], return_usage: bool = False
    ) -> str | tuple[str, dict]:
        """Generate an answer for `query` grounded in `context` chunks."""
        prompt = self._build_prompt(query, context)
        resp = litellm.completion(
            model=f"openai/{self.model}",
            messages=[{"role": "user", "content": prompt}],
            api_base=self.api_base,
            api_key=self.api_key,
            custom_llm_provider="openai",
            timeout=90,
            max_tokens=1024,
        )
        answer = resp.choices[0].message.content
        if not return_usage:
            return answer
        usage = getattr(resp, "usage", None)
        usage_dict = {
            "rag.prompt_tokens": getattr(usage, "prompt_tokens", None),
            "rag.completion_tokens": getattr(usage, "completion_tokens", None),
            "rag.total_tokens": getattr(usage, "total_tokens", None),
        }
        return answer, usage_dict
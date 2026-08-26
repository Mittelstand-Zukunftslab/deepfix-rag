"""vLLM / Qwen / Octen embeddings, re-ranking, and generation client — via LiteLLM and OpenAI APIs."""

import os
from dotenv import load_dotenv
import litellm

load_dotenv()


def _resolve_provider_endpoints(
    provider: str, model: str
) -> tuple[str, str]:
    """Resolve the base URL and API key based on provider and model name."""
    provider_lower = provider.lower() if provider else ""
    model_lower = model.lower() if model else ""

    # AISC / Octen provider or Octen model
    if (
        provider_lower in ("aisc", "octen")
        or "octen" in model_lower
        or "aisc" in model_lower
    ):
        base_url = os.getenv(
            "AISC_URL",
            os.getenv("OCTEN_URL", "https://api.aisc.hpi.de"),
        )
        api_key = os.getenv(
            "AISC_KEY",
            os.getenv("OCTEN_KEY", "sk-5lWnPeBK4omoI8l9UHOjhw"),
        )
        return base_url, api_key

    # Default to LiteLLM / HPI proxy
    base_url = os.getenv("LITELLM_URL", "https://litellm.mittelstand-zukunftslab.de")
    api_key = os.getenv("LITELLM_KEY", "")
    return base_url, api_key


class EmbeddingClient:
    """Client for generating embeddings (Octen-8B, Qwen3 embedding models)."""

    def __init__(
        self,
        model: str = "octen-embedding-8b",
        provider: str = "aisc",
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        resolved_base, resolved_key = _resolve_provider_endpoints(self.provider, self.model)
        self.api_base = api_base or resolved_base
        self.api_key = api_key or resolved_key

    def _normalize_model_name(self) -> str:
        """Format model string for litellm openai provider."""
        model_name = self.model
        if model_name.startswith("Octen/") or model_name.startswith("octen/"):
            model_name = model_name.split("/")[-1]
        if not model_name.startswith("openai/"):
            return f"openai/{model_name}"
        return model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a list of document strings."""
        if not texts:
            return []
        model_str = self._normalize_model_name()
        resp = litellm.embedding(
            model=model_str,
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

    def __init__(
        self,
        model: str = "rerank-english-v3.0",
        provider: str = "hpi",
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        resolved_base, resolved_key = _resolve_provider_endpoints(self.provider, self.model)
        self.api_base = api_base or resolved_base
        self.api_key = api_key or resolved_key

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
    """Client for answer generation via LiteLLM / OpenAI proxy."""

    def __init__(
        self,
        model: str = "openai/openrouter/qwen/qwen3.6-plus",
        provider: str = "hpi",
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        self.provider = os.getenv("DEEPFIX_INFERENCE_PROVIDER", provider)
        resolved_base, resolved_key = _resolve_provider_endpoints(self.provider, self.model)
        self.api_base = api_base or resolved_base
        self.api_key = api_key or resolved_key

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
        model_str = self.model if self.model.startswith("openai/") else f"openai/{self.model}"
        resp = litellm.completion(
            model=model_str,
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

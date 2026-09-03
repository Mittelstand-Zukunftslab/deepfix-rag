"""vLLM / Qwen / Octen embeddings, re-ranking, and generation client — via LiteLLM and OpenAI APIs."""

import os
import time
from dotenv import load_dotenv
import litellm
import requests

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

    # OpenRouter native (only trigger if the provider is explicitly openrouter)
    if provider_lower == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
        api_key = os.getenv("OPENROUTER_KEY", os.getenv("LITELLM_KEY", ""))
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
        
        last_err = None
        # Add rate limit backoff for large indexing workloads
        for attempt in range(5):
            try:
                resp = litellm.embedding(
                    model=model_str,
                    input=texts,
                    api_base=self.api_base,
                    api_key=self.api_key,
                    custom_llm_provider="openai",
                    timeout=90,
                )
                return [item["embedding"] for item in resp["data"]]
            except Exception as e:
                last_err = e
                if "429" in str(e):
                    time.sleep(2 ** attempt)
                else:
                    raise e
        raise last_err

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding vector for a single query."""
        return self.embed_documents([text])[0]


class RerankClient:
    """Client for cross-encoder re-ranking. Direct API implementation for OpenRouter per Fadel implementation request."""

    def __init__(
        self,
        model: str = "qwen/qwen3-reranker-8b",
        provider: str = "openrouter",
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        
        # Override to Fadel's OpenRouter specification directly for exactly reproducible results
        self.api_base = api_base or os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1")
        self.api_key = api_key or os.getenv("OPENROUTER_KEY", os.getenv("LITELLM_KEY", ""))

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[int]:
        """Re-rank candidate documents and return top_n document indices exactly matching OpenRouter's HTTP rerank payload."""
        if not documents:
            return []
            
        endpoint = f"{self.api_base.rstrip('/')}/rerank"
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents))
        }

        last_resp_text = ""
        last_status = 500
        
        # Add explicit backoff handler for OpenRouter rate limits (429 Too Many Requests)
        for attempt in range(5):
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=90)
            
            if resp.ok:
                data = resp.json()
                results = data.get("results", [])
                return [r["index"] for r in results]
                
            last_resp_text = resp.text
            last_status = resp.status_code
            
            if last_status == 429:
                time.sleep(2 ** attempt + 3) # Wait longer between bursts: 4s, 5s, 7s, 11s, 19s
                continue
            else:
                break
                
        raise RuntimeError(f"Reranking API failed: {last_status} - {last_resp_text}")


class GenerationClient:
    """Client for answer generation via LiteLLM / OpenAI proxy."""

    def __init__(
        self,
        model: str = "openai/openrouter/qwen/qwen3.8-27b",
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
        
        last_err = None
        for attempt in range(5):
            try:
                resp = litellm.completion(
                    model=model_str,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=self.api_base,
                    api_key=self.api_key,
                    custom_llm_provider="openai",
                    timeout=90,
                    max_tokens=1024,
                )
                answer = resp.choices[0].message.content or ""
                if not return_usage:
                    return answer
                usage = getattr(resp, "usage", None)
                usage_dict = {
                    "rag.prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "rag.completion_tokens": getattr(usage, "completion_tokens", None),
                    "rag.total_tokens": getattr(usage, "total_tokens", None),
                }
                return answer, usage_dict
            except Exception as e:
                last_err = e
                if "429" in str(e):
                    time.sleep(2 ** attempt + 3)
                else:
                    raise e
        raise last_err

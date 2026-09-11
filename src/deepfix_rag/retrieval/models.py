"""Wrap generation, embedding, and reranking endpoints internally using LiteLLM."""

import json
import logging
import os
import time

from deepfix_rag.config.settings import settings

import litellm

# Explicitly disable LiteLLM telemetry to avoid hangs

litellm.telemetry = False



def _resolve_provider_endpoints(
    provider: str,
) -> tuple[str | None, str | None]:
    if provider == "aisc" or provider == "hpi":
        return settings.OCTEN_URL, (settings.OCTEN_KEY or settings.LITELLM_KEY)
    elif "proxy" in provider or "litellm" in provider:
        return settings.LITELLM_URL, settings.LITELLM_KEY
    return None, None


# ==============================================================================
# RETRIEVAL (EMBEDDING)
# ==============================================================================
from llama_index.core.embeddings import BaseEmbedding
from typing import Any, List
import requests

class AISCEmbedding(BaseEmbedding):
    """Custom LlamaIndex Embedding adapter using AISC/LiteLLM with Exponential Backoff."""
    
    model_name: str
    api_base: str
    api_key: str
    batch_size: int = 32
    
    def __init__(self, model_name: str = "octen-embedding-8b", provider: str = "aisc", **kwargs: Any):
        api_base, api_key = _resolve_provider_endpoints(provider)
        super().__init__(
            model_name=model_name,
            api_base=api_base or "",
            api_key=api_key or "",
            **kwargs,
        )

    @classmethod
    def class_name(cls) -> str:
        return "AISCEmbedding"

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return self._get_text_embedding(text)

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._get_text_embeddings([query])[0]

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._get_text_embeddings([text])[0]

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        retries = 5
        base_delay = 1
        
        for attempt in range(retries):
            try:
                responses = litellm.embedding(
                    model=self.model_name,
                    input=texts,
                    api_key=self.api_key,
                    api_base=self.api_base,
                    custom_llm_provider="openai",
                    timeout=60,
                )
                
                # Extract embeddings in matching order
                embs = [None] * len(texts)
                for datum in responses.data:
                    embs[datum["index"]] = datum["embedding"]
                return embs
                
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "Too Many Requests" in err_str:
                    logger = logging.getLogger(__name__)
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"[Embedding 429] Retrying in {delay}s... ({attempt+1}/{retries})")
                    time.sleep(delay)
                    continue
                raise e
        raise RuntimeError(f"Embedding failed after {retries} retries.")


# ==============================================================================
# RERANKING
# ==============================================================================
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from typing import Optional

class AISCReranker(BaseNodePostprocessor):
    """Custom Cross-Encoder Reranker using direct OpenRouter HTTP POST calls (LlamaIndex wrapper)."""
    
    model: str = "qwen/qwen3-reranker-8b"
    top_n: int = 5
    api_key: str = ""

    def __init__(self, model: str = "qwen/qwen3-reranker-8b", top_n: int = 5, api_key: Optional[str] = None):
        super().__init__(model=model, top_n=top_n, api_key=api_key or settings.OCTEN_KEY or settings.LITELLM_KEY or settings.OPENROUTER_KEY or "")

    @classmethod
    def class_name(cls) -> str:
        return "AISCReranker"
        
    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        if not nodes:
            return []
            
        doc_texts = [node.get_content() for node in nodes]
        query = query_bundle.query_str if query_bundle else ""

        url = "https://api.aisc.hpi.de/v1/rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/aihpi/pilotproject-deepfix",
            "X-Title": "DeepFix RAG Testbed",
        }
        data = {
            "model": self.model,
            "query": query,
            "documents": doc_texts,
            "top_n": self.top_n
        }

        retries = 3
        base_delay = 1.0

        for attempt in range(retries):
            try:
                resp = requests.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 429:
                    delay = base_delay * (2 ** attempt)
                    logging.getLogger(__name__).warning(f"[Rerank 429] Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                
                resp_json = resp.json()
                results = resp_json.get("results", [])
                
                # results looks like: [{"index": 4, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.85}, ...]
                final_nodes = []
                num_returned = len(results)
                
                for rank_idx, result_obj in enumerate(results):
                    original_idx = result_obj["index"]
                    best_node = nodes[original_idx]
                    
                    # Convert dense vector score to rerank score
                    # Either use exact probability relevance_score if available, or synthesized rank
                    rel_score = result_obj.get("relevance_score", float(num_returned - rank_idx))
                    best_node.score = rel_score
                    final_nodes.append(best_node)
                    
                return final_nodes
                
            except requests.exceptions.RequestException as e:
                if attempt == retries - 1:
                    logging.getLogger(__name__).error(f"Reranking API crashed entirely: {str(e)}")
                    return nodes[:self.top_n]
                time.sleep(base_delay * (2 ** attempt))
                
        return nodes[:self.top_n]

# To maintain backwards compatibility while transitioning, export old classes to alias new ones where needed
# (or provide simple wrappers)
def EmbeddingClient(model: str, provider: str):
    # Backward compatibility stub
    emb = AISCEmbedding(model_name=model, provider=provider)
    class Shim:
        def embed_query(self, q): return emb._get_query_embedding(q)
        def embed_documents(self, docs): return emb._get_text_embeddings(docs)
    return Shim()

def RerankClient(model: str):
    reranker = AISCReranker(model=model, top_n=5)
    class Shim:
        def rerank(self, q, docs, top_n=5):
            from llama_index.core.schema import NodeWithScore, TextNode
            nodes = [NodeWithScore(node=TextNode(text=t), score=1.0) for t in docs]
            reranker.top_n = top_n
            out_nodes = reranker._postprocess_nodes(nodes, QueryBundle(q))
            # Just return original indices matched
            return [docs.index(n.get_content()) for n in out_nodes]
    return Shim()

class GenerationClient:
    def __init__(
        self,
        model: str = "qwen3-8-27b",
        provider: str = "aisc",
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        base, key = _resolve_provider_endpoints(provider)
        self.model = model
        self.api_base = api_base or base
        self.api_key = api_key or key
        self.provider = provider

    def generate(
        self, prompt: str, context: list[str] = None, return_usage: bool = False
    ):
        system_msg = (
            "You are a highly precise legal assistant. "
            "Use only the provided context to answer the question. "
            "If the context does not contain the answer, explicitly say "
            "'The provided context does not contain the answer.'"
        )

        context_str = "\\n\\n".join(context) if context else ""
        user_msg = f"Context:\\n{context_str}\\n\\nQuestion: {prompt}\\n\\nAnswer:"

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        retries = 5
        base_delay = 1
        for attempt in range(retries):
            try:
                r = litellm.completion(
                    model=self.model,
                    messages=messages,
                    api_base=self.api_base,
                    api_key=self.api_key,
                    custom_llm_provider="openai",
                    timeout=90,
                )
                answer = r.choices[0].message.content or ""
                usage = r.usage.model_dump() if r.usage else {}
                break
            except Exception as e:
                if "429" in str(e) or "Too Many Requests" in str(e):
                    delay = base_delay * (2**attempt)
                    time.sleep(delay)
                    continue
                raise e
        else:
            raise RuntimeError(f"Generation failed after {retries} retries.")

        return (answer, usage) if return_usage else answer

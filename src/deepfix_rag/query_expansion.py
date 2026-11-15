"""Query expansion through the generation model, one completion per distinct prompt.

``QueryFusionRetriever`` above one query asks an LLM to rewrite the question into
``num_queries - 1`` paraphrases. That is one completion per question per
retrieval pass, so an HPO sweep that samples ``retrieval.query_expansion_num``
would pay for the same rewrite in every trial that draws the same value.
``ExpansionCache`` keys the answer on the prompt text, which carries the
configured ``query_gen_prompt``, the question, and the requested number of
queries. The rewrite therefore depends only on the question and that count, not
on the trial config, so:

* two trials that request the same number of queries share one completion;
* the candidate and the champion in a compare share it too, which is what keeps
  a paired gate honest: both sides see the same paraphrases.

``CachingExpansionLLM`` is the wrapper that applies the cache. It is the only
place an expansion completion is issued, so a caller bounds the cost by passing
one cache to every run it starts.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
import hashlib
import threading
from typing import Any

from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    ChatResponseAsyncGen,
    ChatResponseGen,
    CompletionResponse,
    CompletionResponseAsyncGen,
    CompletionResponseGen,
    LLMMetadata,
)
from llama_index.core.llms.llm import LLM
from pydantic import PrivateAttr

DEFAULT_CACHE_SIZE = 4096


class ExpansionCache:
    """Bounded, thread-safe memo of expansion completions keyed by prompt digest.

    A round runs many questions concurrently (``eval.concurrency``), each in a
    worker thread, so the store is guarded by a lock rather than relying on the
    GIL. The counters are what an HPO result reports back: ``calls`` is the
    number of completions that reached the provider, ``hits`` the number served
    from the cache.
    """

    def __init__(self, size: int = DEFAULT_CACHE_SIZE) -> None:
        self._size = max(1, size)
        self._values: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self.calls = 0
        self.hits = 0

    @staticmethod
    def key(prompt: str) -> str:
        """Digest of a prompt, so the cache does not hold the question text twice."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def get(self, prompt: str) -> str | None:
        with self._lock:
            value = self._values.get(self.key(prompt))
            if value is None:
                return None
            self._values.move_to_end(self.key(prompt))
            self.hits += 1
            return value

    def put(self, prompt: str, text: str) -> None:
        with self._lock:
            self.calls += 1
            self._values[self.key(prompt)] = text
            self._values.move_to_end(self.key(prompt))
            while len(self._values) > self._size:
                self._values.popitem(last=False)

    def stats(self) -> dict[str, int]:
        """Completions sent upstream, cache hits, and distinct prompts held."""
        with self._lock:
            return {
                "expansion_llm_calls": self.calls,
                "expansion_cache_hits": self.hits,
                "expansion_prompts_cached": len(self._values),
            }


class CachingExpansionLLM(LLM):
    """An ``LLM`` that answers an expansion prompt once and replays it after that.

    It subclasses ``LLM`` rather than ``BaseLLM`` because ``QueryFusionRetriever``
    resolves its ``llm`` argument through ``resolve_llm``, which asserts the
    concrete ``LLM`` type. Only ``complete`` and ``acomplete`` are cached,
    because that is the call the fusion retriever makes. A call carrying extra
    keyword arguments is passed through uncached: the same prompt under different
    sampling options is a different completion. Streaming and chat delegate.
    """

    model: str = ""

    _inner: LLM = PrivateAttr()
    _cache: ExpansionCache = PrivateAttr()

    def __init__(self, inner: LLM, cache: ExpansionCache) -> None:
        super().__init__(model=str(getattr(inner.metadata, "model_name", "") or ""))
        self._inner = inner
        self._cache = cache

    @property
    def metadata(self) -> LLMMetadata:
        return self._inner.metadata

    @property
    def cache(self) -> ExpansionCache:
        return self._cache

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        if kwargs:
            return self._inner.complete(prompt, formatted=formatted, **kwargs)
        cached = self._cache.get(prompt)
        if cached is not None:
            return CompletionResponse(text=cached)
        response = self._inner.complete(prompt, formatted=formatted)
        self._cache.put(prompt, response.text)
        return response

    async def acomplete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponse:
        if kwargs:
            return await self._inner.acomplete(prompt, formatted=formatted, **kwargs)
        cached = self._cache.get(prompt)
        if cached is not None:
            return CompletionResponse(text=cached)
        response = await self._inner.acomplete(prompt, formatted=formatted)
        self._cache.put(prompt, response.text)
        return response

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return self._inner.chat(messages, **kwargs)

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return await self._inner.achat(messages, **kwargs)

    def stream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseGen:
        return self._inner.stream_complete(prompt, formatted=formatted, **kwargs)

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        return self._inner.stream_chat(messages, **kwargs)

    async def astream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseAsyncGen:
        return await self._inner.astream_complete(prompt, formatted=formatted, **kwargs)

    async def astream_chat(
        self, messages: Sequence[ChatMessage], **kwargs: Any
    ) -> ChatResponseAsyncGen:
        return await self._inner.astream_chat(messages, **kwargs)

import asyncio
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
import litellm

from deepfix_rag.llm import configure_litellm_drop_params, resolve_litellm_model


class ProxyJudgeLLM(DeepEvalBaseLLM):
    def __init__(
        self,
        model: str,
        api_base: str,
        api_key: str,
        provider: str = "openai",
        max_tokens: int = 4096,
        timeout: int = 90,
    ) -> None:
        self.model = resolve_litellm_model(model, api_base)
        self.api_base = api_base
        self.api_key = api_key
        self.provider = provider
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.drop_params = configure_litellm_drop_params()

    def load_model(self):
        return self.model

    def _completion_kwargs(self, prompt: str) -> dict[str, Any]:
        """Arguments for ``litellm.completion``.

        An ``openrouter/`` model id already selects the provider. Passing
        ``custom_llm_provider`` as well makes LiteLLM prepend that provider,
        and the OpenRouter early return drops ``api_key``.
        """
        self.drop_params = configure_litellm_drop_params()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "api_base": self.api_base,
            "api_key": self.api_key,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "drop_params": self.drop_params,
        }
        if not self.model.startswith("openrouter/"):
            kwargs["custom_llm_provider"] = self.provider
        return kwargs

    def generate(self, prompt: str, schema=None) -> str:
        r = litellm.completion(**self._completion_kwargs(prompt))
        val = r.choices[0].message.content or ""
        val = val.strip()
        import re

        match = re.search(r"\{.*\}", val, re.DOTALL)
        if match:
            val = match.group(0)
        return val

    async def a_generate(self, prompt: str, schema=None) -> str:
        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self):
        return self.model

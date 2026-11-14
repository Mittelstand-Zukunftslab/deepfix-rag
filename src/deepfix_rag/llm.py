"""Shared LiteLLM options for every completion path in this package.

Answer generation, the DeepEval judge, and evaluation all call LiteLLM.
Some models (a Claude Haiku, an Opus) reject ``temperature`` other than 1.
``litellm.drop_params`` omits those arguments instead of failing the call.

OpenRouter model ids keep the upstream provider in the first segment
(``vendor/model-name``). LiteLLM reads that segment as the provider
unless the id starts with ``openrouter/``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import litellm

from deepfix_rag.config.settings import LOGGER, settings

DROP_PARAMS_ENV = "DEEPFIX_LLM_DROP_PARAMS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "t", "y"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "f", "n"})


def drop_params_enabled() -> bool:
    """Return whether LiteLLM should omit parameters the provider rejects.

    ``DEEPFIX_LLM_DROP_PARAMS`` overrides the setting. Unset means the
    ``Settings`` default, which is enabled. ``1``, ``true``, ``yes``, and
    ``on`` enable it; ``0``, ``false``, ``no``, and ``off`` disable it.
    """
    raw = os.environ.get(DROP_PARAMS_ENV)
    if raw is None or raw.strip() == "":
        return bool(settings.DEEPFIX_LLM_DROP_PARAMS)
    token = raw.strip().lower()
    if token in _TRUE_VALUES:
        return True
    if token in _FALSE_VALUES:
        return False
    LOGGER.warning(
        "%s=%r is not a flag; dropping unsupported LiteLLM params.",
        DROP_PARAMS_ENV,
        raw,
    )
    return True


def configure_litellm_drop_params() -> bool:
    """Set the process-wide ``litellm.drop_params`` flag and return it."""
    enabled = drop_params_enabled()
    litellm.drop_params = enabled
    return enabled


def resolve_litellm_model(model: str, base_url: str | None) -> str:
    """Return the model id LiteLLM should route for this endpoint.

    An OpenRouter base URL (host ending in ``openrouter.ai``) is prefixed with
    ``openrouter/`` unless the id already has that prefix. On any other
    OpenAI-compatible endpoint, a bare name with no ``/`` keeps the ``openai/``
    prefix. Ids that already name a provider are left unchanged.
    """
    host = urlparse(base_url).hostname if base_url else None
    if host and host.endswith("openrouter.ai") and not model.startswith("openrouter/"):
        return f"openrouter/{model}"
    if not (model.startswith("openai/") or "/" in model):
        return f"openai/{model}"
    return model


def litellm_additional_kwargs(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kwargs for a LlamaIndex LiteLLM client, including ``drop_params``."""
    kwargs = dict(extra or {})
    kwargs["drop_params"] = configure_litellm_drop_params()
    return kwargs


# Apply once on import so later litellm calls in this process drop unsupported
# params unless the operator turned the flag off.
configure_litellm_drop_params()

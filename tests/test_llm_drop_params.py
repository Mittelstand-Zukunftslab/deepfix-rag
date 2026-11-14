"""LiteLLM drops provider-rejected params without calling the network."""

import os

import litellm
from litellm.exceptions import UnsupportedParamsError
from litellm.utils import get_optional_params
import pytest

from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.llm import configure_litellm_drop_params
from deepfix_rag.pipeline import RAGWorkflow

_ENV = "DEEPFIX_LLM_DROP_PARAMS"


def _restore(previous: str | None) -> None:
    if previous is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = previous
    configure_litellm_drop_params()


@pytest.mark.unit
def test_constructed_llm_drops_unsupported_params() -> None:
    """Building the answer model and the judge enables drop_params."""
    previous = os.environ.pop(_ENV, None)
    try:
        llm = RAGWorkflow()._get_llm()
        assert llm.additional_kwargs["drop_params"] is True
        assert litellm.drop_params is True

        judge = ProxyJudgeLLM(
            model="openai/gpt-test",
            api_base="http://localhost",
            api_key="test",
        )
        assert judge.drop_params is True
        assert litellm.drop_params is True
    finally:
        _restore(previous)


@pytest.mark.unit
def test_haiku_temperature_zero_is_dropped_offline() -> None:
    """OpenRouter's Claude Haiku 5.5 id rejects temperature=0 until the flag is on."""
    model, provider, _, _ = litellm.get_llm_provider(
        model="anthropic/claude-haiku-5.5",
        api_base="https://openrouter.ai/api/v1",
    )
    assert (model, provider) == ("claude-haiku-5.5", "anthropic")

    previous = os.environ.get(_ENV)
    try:
        os.environ[_ENV] = "0"
        assert configure_litellm_drop_params() is False
        with pytest.raises(UnsupportedParamsError):
            get_optional_params(
                model=model,
                custom_llm_provider=provider,
                temperature=0.0,
                drop_params=False,
            )

        os.environ[_ENV] = "1"
        assert configure_litellm_drop_params() is True
        dropped = get_optional_params(
            model=model,
            custom_llm_provider=provider,
            temperature=0.0,
        )
        assert "temperature" not in dropped

        litellm.drop_params = False
        per_call = get_optional_params(
            model=model,
            custom_llm_provider=provider,
            temperature=0.0,
            drop_params=True,
        )
        assert "temperature" not in per_call

        os.environ[_ENV] = "0"
        disabled = RAGWorkflow()._get_llm()
        assert disabled.additional_kwargs["drop_params"] is False
        assert litellm.drop_params is False
    finally:
        _restore(previous)

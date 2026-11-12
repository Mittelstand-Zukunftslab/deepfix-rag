"""LiteLLM routes OpenRouter model ids without calling the network."""

from collections.abc import Iterator
from contextlib import contextmanager

import litellm
import pytest

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import ModelNotConfigured, settings
from deepfix_rag.evaluation.llm import ProxyJudgeLLM
from deepfix_rag.evaluation.runner import GenerationEvaluator
from deepfix_rag.llm import resolve_litellm_model
from deepfix_rag.pipeline import RAGWorkflow

_OPENROUTER = "https://openrouter.ai/api/v1"
_HAIKU = "anthropic/claude-haiku-5.5"
_ROUTED_HAIKU = "openrouter/anthropic/claude-haiku-5.5"


@contextmanager
def _generation_settings(model: str, base_url: str, api_key: str) -> Iterator[None]:
    previous = (
        settings.LLM_MODEL,
        settings.LLM_BASE_URL,
        settings.LLM_API_KEY,
        settings.LLM_JUDGE_MODEL,
    )
    settings.LLM_MODEL = model
    settings.LLM_BASE_URL = base_url
    settings.LLM_API_KEY = api_key
    settings.LLM_JUDGE_MODEL = model
    try:
        yield
    finally:
        (
            settings.LLM_MODEL,
            settings.LLM_BASE_URL,
            settings.LLM_API_KEY,
            settings.LLM_JUDGE_MODEL,
        ) = previous


@pytest.mark.unit
def test_resolve_litellm_model_prefixes_openrouter_provider_ids() -> None:
    assert resolve_litellm_model(_HAIKU, _OPENROUTER) == _ROUTED_HAIKU
    assert resolve_litellm_model("qwen3-8-27b", _OPENROUTER) == "openrouter/qwen3-8-27b"
    assert resolve_litellm_model(_HAIKU, "https://OpenRouter.AI/api/v1") == _ROUTED_HAIKU


@pytest.mark.unit
def test_resolve_litellm_model_leaves_an_openrouter_prefix_unchanged() -> None:
    assert resolve_litellm_model(_ROUTED_HAIKU, _OPENROUTER) == _ROUTED_HAIKU


@pytest.mark.unit
def test_resolve_litellm_model_prefixes_bare_names_off_openrouter() -> None:
    assert resolve_litellm_model("qwen3-8-27b", "https://api.aisc.hpi.de/v1") == (
        "openai/qwen3-8-27b"
    )
    assert resolve_litellm_model("gpt-test", None) == "openai/gpt-test"
    assert resolve_litellm_model(_HAIKU, "https://api.anthropic.com") == _HAIKU
    assert resolve_litellm_model(_HAIKU, "https://openrouter.ai.evil.example/v1") == _HAIKU


@pytest.mark.unit
def test_openrouter_prefixed_id_selects_the_openrouter_provider() -> None:
    _model, provider, _key, _base = litellm.get_llm_provider(_ROUTED_HAIKU)
    assert provider == "openrouter"


@pytest.mark.unit
def test_answer_llm_and_judge_use_the_routed_openrouter_model() -> None:
    with _generation_settings(_HAIKU, _OPENROUTER, "test-key"):
        llm = RAGWorkflow()._get_llm()
        assert llm.model == _ROUTED_HAIKU
        assert llm.additional_kwargs["api_base"] == _OPENROUTER
        assert llm.additional_kwargs["api_key"] == "test-key"
        assert "custom_llm_provider" not in llm.additional_kwargs

        judge = GenerationEvaluator(generation_metrics=[]).judge
        assert isinstance(judge, ProxyJudgeLLM)
        assert judge.model == _ROUTED_HAIKU
        assert judge.api_base == _OPENROUTER
        assert judge.api_key == "test-key"

    kwargs = judge._completion_kwargs("hello")
    assert kwargs["model"] == _ROUTED_HAIKU
    assert kwargs["api_base"] == _OPENROUTER
    assert kwargs["api_key"] == "test-key"
    assert "custom_llm_provider" not in kwargs

    routed_model, provider, key, base = litellm.get_llm_provider(
        model=kwargs["model"],
        api_base=kwargs["api_base"],
        api_key=kwargs["api_key"],
    )
    assert (routed_model, provider, key, base) == (_HAIKU, "openrouter", "test-key", _OPENROUTER)


@pytest.mark.unit
def test_judge_keeps_the_openai_provider_off_openrouter() -> None:
    judge = ProxyJudgeLLM(model="gpt-test", api_base="http://localhost:9/v1", api_key="test-key")
    assert judge.model == "openai/gpt-test"
    kwargs = judge._completion_kwargs("hello")
    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["api_base"] == "http://localhost:9/v1"
    assert kwargs["api_key"] == "test-key"


@pytest.mark.unit
def test_embeddings_and_reranker_keep_their_own_model_ids() -> None:
    workflow = RAGWorkflow(config=RAGConfig(rerank={"enabled": True}))
    embed = workflow._get_embed_model()
    assert embed.model_name == settings.EMBEDDING_MODEL
    assert embed.api_base == settings.EMBEDDING_BASE_URL

    reranker = workflow._get_reranker()
    assert reranker is not None
    assert reranker.model == settings.COHERE_MODEL
    # The Cohere client holds the endpoint; the public field stays at its default.
    assert reranker._client._client_wrapper._base_url == settings.COHERE_BASE_URL


@pytest.mark.unit
def test_answer_and_judge_models_follow_their_env_vars() -> None:
    """LLM_MODEL and LLM_JUDGE_MODEL are independent and come from the environment."""
    with _generation_settings("qwen/fixture-answer", _OPENROUTER, "test-key"):
        settings.LLM_JUDGE_MODEL = "z-ai/fixture-judge"
        llm = RAGWorkflow()._get_llm()
        judge = GenerationEvaluator(generation_metrics=[]).judge
    assert llm.model == "openrouter/qwen/fixture-answer"
    assert isinstance(judge, ProxyJudgeLLM)
    assert judge.model == "openrouter/z-ai/fixture-judge"


@pytest.mark.unit
def test_a_missing_generation_or_judge_model_names_the_variable() -> None:
    """No hardcoded fallback: the pipeline and the judge fail naming the variable."""
    with _generation_settings("", _OPENROUTER, "test-key"):
        assert settings.LLM_JUDGE_MODEL == ""
        with pytest.raises(ModelNotConfigured, match="LLM_MODEL"):
            RAGWorkflow()._get_llm()
        with pytest.raises(ModelNotConfigured, match="LLM_JUDGE_MODEL"):
            GenerationEvaluator(generation_metrics=[])

"""A sweep that samples query expansion runs end to end and pays for it once.

The shipped small space can draw ``retrieval.query_expansion_num`` 2 or 3, which
makes ``QueryFusionRetriever`` ask the generation model for paraphrases. Two
things have to hold:

* a trial with expansion 2 scores instead of erroring, and
* the paraphrase of a question is asked for once per requested count, not once
  per trial, because an HPO sweep would otherwise multiply the completion bill
  by its trial count.

Both are checked against a local OpenAI-compatible endpoint
(``tests.stub_model_server``), so no network and no mock of domain code: the
completion counter is the evidence, and it is the same counter the HPO result
reports as ``expansion_llm_calls``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from llama_index.core.embeddings import MockEmbedding
from llama_index.llms.litellm import LiteLLM
import pytest

from deepfix_rag.config.hpo import HPOConfig, HPOSearchSpace
from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig, RetrievalBackend
from deepfix_rag.hpo import HyperparameterOpt
from deepfix_rag.pipeline import RAGWorkflow
from tests.stub_model_server import StubModelServer

pytestmark = [pytest.mark.integration]

EMBED_DIM = 16
EXPANSION = 2


def _index(sample_corpus_path: Path, tmp_path: Path) -> Path:
    index_dir = tmp_path / "expansion_index"
    config = RAGConfig()
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))

    async def _build() -> None:
        workflow = RAGWorkflow(config=config, embed_model=MockEmbedding(embed_dim=EMBED_DIM))
        await workflow.run(ingest=True)

    asyncio.run(_build())
    return index_dir


def _space() -> HPOConfig:
    """A one-configuration space that always expands, so the cost is countable."""
    return HPOConfig(
        search_space=HPOSearchSpace.model_validate(
            {
                "hybrid": {
                    "top_k_choices": [3],
                    "query_expansion_num_min": EXPANSION,
                    "query_expansion_num_max": EXPANSION,
                    "rerank_choices": [False],
                    "fusion_modes": ["reciprocal_rerank"],
                    "tune_bm25_params": False,
                }
            }
        )
    )


def _base(index_dir: Path, corpus_path: Path) -> RAGConfig:
    config = RAGConfig()
    config.retrieval.backend = RetrievalBackend.HYBRID
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = True
    config.retrieval.top_k = 3
    config.retrieval_only = True
    config.data = DataLoaderConfig(path_or_repo=str(corpus_path))
    return config


def test_an_hpo_trial_with_expansion_scores_and_expands_each_question_once(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    index_dir = _index(sample_corpus_path, tmp_path)
    questions = 2

    with StubModelServer() as server:
        llm = LiteLLM(
            model="openai/stub-expansion",
            api_base=server.base_url,
            api_key="stub-key",
            temperature=0.0,
            max_tokens=64,
        )
        opt = HyperparameterOpt(
            base_config=_base(index_dir, sample_corpus_path),
            hpo_config=_space(),
            study_name="expansion_cost",
            storage_uri="sqlite:///:memory:",
            embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            llm=llm,
            persist=False,
        )
        result = opt.optimize(
            n_trials=1,
            dataset_path=str(sample_qa_path),
            limit=questions,
        )

        completed = [trial for trial in result.study.trials if trial.state.name == "COMPLETE"]
        assert completed, [trial.state.name for trial in result.study.trials]
        assert result.best_trial is not None
        assert result.best_trial.params["retrieval.query_expansion_num"] == EXPANSION
        # The trial scored; an expansion failure would have errored every case.
        assert all(value is not None for value in result.best_trial.values)

        # One completion per question for this expansion count...
        assert result.expansion_llm_calls == questions
        assert server.completion_count() == questions
        assert result.expansion_cache_hits == 0

        # ...and a second trial with the same count reuses every one of them.
        again = HyperparameterOpt(
            base_config=_base(index_dir, sample_corpus_path),
            hpo_config=_space(),
            study_name="expansion_cost_repeat",
            storage_uri="sqlite:///:memory:",
            embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            llm=llm,
            persist=False,
        )
        again.expansion_cache = opt.expansion_cache
        repeat = again.optimize(n_trials=1, dataset_path=str(sample_qa_path), limit=questions)

    assert repeat.expansion_llm_calls == 0
    assert repeat.expansion_cache_hits == questions
    assert server.completion_count() == questions

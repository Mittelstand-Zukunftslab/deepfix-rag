"""A retrieval-only eval scores without a generation model when it needs none.

``QueryFusionRetriever`` only calls an LLM when it expands the query, so the
fusion retriever must not be constructed with ``_get_llm()`` at
``retrieval.query_expansion_num <= 1``. Generation and expansion above one
still fail by naming ``LLM_MODEL``.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path

from llama_index.core.embeddings import MockEmbedding
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig, RetrievalBackend
from deepfix_rag.config.settings import ModelNotConfigured, settings
from deepfix_rag.evaluation.runner import run_evaluation
from deepfix_rag.main import app
from deepfix_rag.pipeline import RAGWorkflow

pytestmark = [pytest.mark.integration]

EMBED_DIM = 16


@contextmanager
def _without_generation_model() -> Iterator[None]:
    """Clear the generation model the way a stripped Hermes terminal does."""
    previous = (settings.LLM_MODEL, settings.LLM_API_KEY)
    settings.LLM_MODEL = ""
    settings.LLM_API_KEY = None
    try:
        yield
    finally:
        settings.LLM_MODEL, settings.LLM_API_KEY = previous


@pytest.fixture
def fixture_index_dir(sample_corpus_path: Path, tmp_path: Path) -> Path:
    """A local LanceDB index of the sample corpus, embedded without a network call."""
    index_dir = tmp_path / "fixture_lancedb_index"
    config = RAGConfig()
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))

    async def _build() -> None:
        workflow = RAGWorkflow(config=config, embed_model=MockEmbedding(embed_dim=EMBED_DIM))
        await workflow.run(ingest=True)

    asyncio.run(_build())
    return index_dir


def _config(
    index_dir: Path,
    backend: RetrievalBackend,
    corpus_path: Path,
    expansion: int = 1,
) -> RAGConfig:
    config = RAGConfig()
    config.retrieval.backend = backend
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = True
    config.retrieval.top_k = 3
    config.retrieval.query_expansion_num = expansion
    config.retrieval_only = True
    config.data = DataLoaderConfig(path_or_repo=str(corpus_path))
    return config


@pytest.mark.parametrize(
    "backend",
    [RetrievalBackend.DENSE, RetrievalBackend.BM25, RetrievalBackend.HYBRID],
)
def test_retrieval_only_eval_runs_without_a_generation_model(
    backend: RetrievalBackend,
    fixture_index_dir: Path,
    sample_corpus_path: Path,
    sample_qa_path: Path,
) -> None:
    config = _config(fixture_index_dir, backend, sample_corpus_path)
    with _without_generation_model():
        summary = asyncio.run(
            run_evaluation(
                dataset_path=str(sample_qa_path),
                config=config,
                retrieval_only=True,
                persist=False,
                limit=2,
                embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            )
        )
    assert summary.total == 2
    assert summary.errors == 0
    assert summary.metrics["ndcg"] is not None


@pytest.mark.parametrize(
    "backend",
    [RetrievalBackend.HYBRID, RetrievalBackend.MIXED],
)
def test_query_expansion_above_one_still_requires_the_generation_model(
    backend: RetrievalBackend,
    fixture_index_dir: Path,
    sample_corpus_path: Path,
    sample_qa_path: Path,
) -> None:
    config = _config(fixture_index_dir, backend, sample_corpus_path, expansion=2)
    with _without_generation_model(), pytest.raises(ModelNotConfigured, match="LLM_MODEL"):
        asyncio.run(
            run_evaluation(
                dataset_path=str(sample_qa_path),
                config=config,
                retrieval_only=True,
                persist=False,
                limit=1,
                embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            )
        )


def test_eval_cli_reports_missing_model_for_query_expansion(
    cli_runner: CliRunner,
    fixture_index_dir: Path,
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    """`deepfix eval` keeps the missing-model error type instead of per-case errors."""
    config_path = tmp_path / "expanded.yaml"
    config_path.write_text(
        _config(
            fixture_index_dir, RetrievalBackend.HYBRID, sample_corpus_path, expansion=2
        ).to_yaml()
    )

    with _without_generation_model():
        result = cli_runner.invoke(
            app,
            [
                "eval",
                "--config",
                str(config_path),
                "--dataset",
                str(sample_qa_path),
                "--retrieval-only",
                "--no-mlflow",
                "--json",
            ],
        )

    assert result.exit_code == EXIT_VALIDATION_ERROR, result.output
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "missing_model"
    assert "LLM_MODEL" in payload["error"]

"""HPO trials stay cheap and resumable: seeded subsample, timeout, local index.

Trials here score the sample fixtures with a stub embedding model, so no
network call and no live index are involved.
"""

import asyncio
import json
from pathlib import Path

from llama_index.core.embeddings import MockEmbedding
import optuna
import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.hpo import HPOConfig, HPOSearchSpace
from deepfix_rag.config.schema import (
    DataLoaderConfig,
    FusionMode,
    RAGConfig,
    RetrievalBackend,
)
from deepfix_rag.evaluation.runner import EvaluationRunner, resolve_eval_records
from deepfix_rag.hpo import (
    INDEX_CACHE_DIRNAME,
    TRIALS_ARTIFACT_NAME,
    HyperparameterOpt,
    TrialEvaluationError,
    copy_index_tree,
    index_cache_path,
    is_remote_index_uri,
)
from deepfix_rag.main import app
from deepfix_rag.pipeline import RAGWorkflow

pytestmark = [pytest.mark.integration]

EMBED_DIM = 16


def _query_time_space(*, fixed: bool = False) -> HPOConfig:
    """An HPO space with only query-time knobs and expansion pinned at 1."""
    low, high = (3, 3) if fixed else (3, 5)
    space = HPOSearchSpace.model_validate(
        {
            "hybrid": {
                "top_k_min": low,
                "top_k_max": high,
                "query_expansion_num_min": 1,
                "query_expansion_num_max": 1,
                "bm25_k1_min": 1.5,
                "bm25_k1_max": 1.5,
                "bm25_b_min": 0.75,
                "bm25_b_max": 0.75,
                "dense_weight_min": 0.5,
                "dense_weight_max": 0.5,
                "fusion_modes": [FusionMode.RECIPROCAL_RANK.value],
                "strategies": ["sentence"],
            }
        }
    )
    return HPOConfig(search_space=space)


def _fixture_index(sample_corpus_path: Path, tmp_path: Path) -> Path:
    index_dir = tmp_path / "base_index"
    config = RAGConfig()
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = False
    config.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))

    async def _build() -> None:
        workflow = RAGWorkflow(config=config, embed_model=MockEmbedding(embed_dim=EMBED_DIM))
        await workflow.run(ingest=True)

    asyncio.run(_build())
    return index_dir


def _base_config(index_dir: Path, corpus_path: Path) -> RAGConfig:
    config = RAGConfig()
    config.retrieval.backend = RetrievalBackend.HYBRID
    config.retrieval.lancedb_index_dir = str(index_dir)
    config.retrieval.load_if_exists = True
    config.retrieval.top_k = 3
    config.retrieval_only = True
    config.data = DataLoaderConfig(path_or_repo=str(corpus_path))
    return config


def _opt(
    index_dir: Path,
    corpus_path: Path,
    *,
    study_name: str = "hpo_test",
    hpo_config: HPOConfig | None = None,
    seed: int | None = None,
    run_dir: Path | None = None,
    output_path: Path | None = None,
    storage_uri: str = "sqlite:///:memory:",
) -> HyperparameterOpt:
    return HyperparameterOpt(
        base_config=_base_config(index_dir, corpus_path),
        hpo_config=hpo_config or _query_time_space(),
        study_name=study_name,
        storage_uri=storage_uri,
        seed=seed,
        run_dir=run_dir,
        output_path=output_path,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        persist=False,
    )


def test_the_seed_reaches_every_trial_sample(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    """A seeded trial scores the same rows as `deepfix eval --seed`.

    The space is degenerate, so the only thing that can move a score is which
    rows were sampled. The trial values therefore have to equal a direct
    ``EvaluationRunner.run(seed=...)`` over the same config.
    """
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    result = _opt(
        index_dir,
        sample_corpus_path,
        hpo_config=_query_time_space(fixed=True),
        seed=7,
    ).optimize(n_trials=1, dataset_path=str(sample_qa_path), limit=2)

    repeated = _opt(
        index_dir,
        sample_corpus_path,
        study_name="hpo_test_repeat",
        hpo_config=_query_time_space(fixed=True),
        seed=7,
    ).optimize(n_trials=1, dataset_path=str(sample_qa_path), limit=2)

    cutoff = RAGConfig().eval.cutoff_k
    direct = asyncio.run(
        EvaluationRunner(
            config=_base_config(index_dir, sample_corpus_path),
            embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            persist=False,
        ).run(dataset_path=str(sample_qa_path), limit=2, seed=7)
    )
    direct_scores = [
        HyperparameterOpt.extract_metric(direct.metrics, name, cutoff_k=cutoff)
        for name in result.metric_names
    ]

    assert list(result.study.trials[0].values) == direct_scores
    assert list(result.study.trials[0].values) == list(repeated.study.trials[0].values)


def test_a_seed_picks_a_different_subsample_than_another_seed(sample_qa_path: Path) -> None:
    """The seed is a real lever on the subsample, not a no-op flag."""
    seeded_two = resolve_eval_records(str(sample_qa_path), None, split="dev", limit=1, seed=2)
    seeded_zero = resolve_eval_records(str(sample_qa_path), None, split="dev", limit=1, seed=0)
    assert [row.query_id for row in seeded_two] != [row.query_id for row in seeded_zero]
    assert [row.query_id for row in seeded_two] == [
        row.query_id
        for row in resolve_eval_records(str(sample_qa_path), None, split="dev", limit=1, seed=2)
    ]


def test_the_run_directory_receives_trials_and_a_best_so_far_config(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    output = run_dir / "candidate.yaml"
    result = _opt(
        index_dir,
        sample_corpus_path,
        seed=0,
        run_dir=run_dir,
        output_path=output,
    ).optimize(n_trials=2, dataset_path=str(sample_qa_path), limit=2)

    trials_path = run_dir / TRIALS_ARTIFACT_NAME
    assert trials_path.is_file()
    rows = json.loads(trials_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    for row in rows:
        assert set(row) >= {
            "trial_number",
            "state",
            "params",
            "values",
            "metrics",
            "user_attrs",
        }
        assert row["state"] == "COMPLETE"
        assert row["metrics"]
    assert output.is_file()
    best = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert best["retrieval"]["backend"] == "hybrid"
    assert result.trials_path == trials_path
    assert result.best_config is not None


def test_a_timeout_stops_the_study_and_keeps_the_artifacts(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    output = run_dir / "candidate.yaml"
    result = _opt(
        index_dir,
        sample_corpus_path,
        seed=0,
        run_dir=run_dir,
        output_path=output,
    ).optimize(n_trials=12, dataset_path=str(sample_qa_path), limit=2, timeout=0.001)

    complete = [t for t in result.study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    assert len(complete) < 12
    assert result.stopped_early is True
    assert (run_dir / TRIALS_ARTIFACT_NAME).is_file()
    if complete:
        assert output.is_file()


def test_a_file_storage_uri_resumes_with_load_if_exists(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    study_db = run_dir / "hpo.db"
    storage_uri = f"sqlite:///{study_db}"

    first = _opt(
        index_dir,
        sample_corpus_path,
        study_name="resume_study",
        storage_uri=storage_uri,
        seed=0,
        run_dir=run_dir,
    ).optimize(n_trials=1, dataset_path=str(sample_qa_path), limit=2)
    assert len(first.study.trials) == 1
    assert study_db.is_file()
    first_params = dict(first.study.trials[0].params)

    second = _opt(
        index_dir,
        sample_corpus_path,
        study_name="resume_study",
        storage_uri=storage_uri,
        seed=0,
        run_dir=run_dir,
    ).optimize(n_trials=2, dataset_path=str(sample_qa_path), limit=2)
    assert len(second.study.trials) == 2
    assert dict(second.study.trials[0].params) == first_params
    assert [t.number for t in second.study.trials] == [0, 1]


def test_a_remote_index_is_copied_once_into_the_run_directory(
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cache = run_dir / INDEX_CACHE_DIRNAME

    assert is_remote_index_uri("s3://deepfix-index/optimization/runs/abc/input")
    assert not is_remote_index_uri(str(index_dir))
    copy_index_tree(str(index_dir), cache)
    assert (cache / "deepfix_index.json").is_file()

    config = RAGConfig()
    config.retrieval.lancedb_index_dir = "s3://deepfix-index/optimization/runs/abc/input"
    opt = HyperparameterOpt(
        base_config=config,
        hpo_config=_query_time_space(),
        study_name="cache_test",
        run_dir=run_dir,
        index_dir=cache,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        persist=False,
    )
    assert opt.local_index_for() == cache
    # A second call in the same process does not copy again.
    assert opt.local_index_for() == cache
    overrides = opt._base_overrides()
    assert overrides["retrieval.lancedb_index_dir"] == str(cache)
    assert overrides["retrieval.load_if_exists"] is True


def test_the_default_cache_location_is_under_the_run_directory() -> None:
    """A remote base index copies to <run_dir>/idx-cache, not a shared volume path."""
    run_dir = Path("/tmp/runs/abc")
    assert index_cache_path(run_dir) == run_dir / "idx-cache"
    assert run_dir in index_cache_path(run_dir).parents


@pytest.mark.cli
def test_hpo_help_lists_seed_timeout_and_the_index_flags(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["hpo", "--help"])
    assert result.exit_code == EXIT_SUCCESS
    for flag in ("--seed", "--timeout", "--index-dir", "--run-dir"):
        assert flag in result.stdout, flag


class _BrokenRerank:
    """A rerank model id that resolves but whose endpoint is unreachable.

    Every case errors, which is what a stripped key or a 404 on a rerank base
    URL looks like. The trial must be FAILED, not scored 0.0.
    """

    def __init__(self) -> None:
        from deepfix_rag.config import settings as settings_module

        self.settings = settings_module.settings

    def __enter__(self) -> "_BrokenRerank":
        self.previous = (
            self.settings.COHERE_MODEL,
            self.settings.COHERE_BASE_URL,
            self.settings.OPENROUTER_API_KEY,
            self.settings.COHERE_API_KEY,
        )
        self.settings.COHERE_MODEL = "cohere/rerank-4-fast"
        # A closed port: the request fails before any ranking happens.
        self.settings.COHERE_BASE_URL = "http://127.0.0.1:9/v1"
        self.settings.OPENROUTER_API_KEY = "sk-or-unreachable-test"
        self.settings.COHERE_API_KEY = None
        return self

    def __exit__(self, *_exc: object) -> None:
        (
            self.settings.COHERE_MODEL,
            self.settings.COHERE_BASE_URL,
            self.settings.OPENROUTER_API_KEY,
            self.settings.COHERE_API_KEY,
        ) = self.previous


def test_a_trial_whose_cases_all_errored_fails_instead_of_scoring_zero(
    sample_corpus_path: Path,
    sample_qa_path: Path,
    tmp_path: Path,
) -> None:
    """A broken rerank endpoint never becomes a 0.0 trial that loses to rerank-off."""
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    space = _query_time_space()
    space.search_space.hybrid.rerank_choices = [True]
    space.search_space.hybrid.rerank_top_n_choices = [5]
    space.search_space.hybrid.top_k_choices = [10]
    space.search_space.hybrid.fusion_modes = [space.search_space.hybrid.fusion_modes[0]]

    with _BrokenRerank(), pytest.raises(TrialEvaluationError, match="no trial completed"):
        _opt(
            index_dir,
            sample_corpus_path,
            hpo_config=space,
            seed=0,
            run_dir=run_dir,
        ).optimize(n_trials=1, dataset_path=str(sample_qa_path), limit=1)

    rows = json.loads((run_dir / TRIALS_ARTIFACT_NAME).read_text(encoding="utf-8"))
    assert rows and all(row["state"] == "FAIL" for row in rows)
    assert "errored" in rows[0]["user_attrs"]["error"]


@pytest.mark.cli
def test_base_config_overrides_the_space_files_base_config(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """The agent passes the champion from the run bundle; the space file need not name it.

    The space's own ``base_config`` points at a file that does not exist. The
    run therefore only gets past config resolution when ``--base-config`` was
    honoured, and the failure it reaches is about the dataset, not that path.
    """
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    champion_path = tmp_path / "champion.yaml"
    champion_path.write_text(_base_config(index_dir, sample_corpus_path).to_yaml())

    space_path = tmp_path / "space.yaml"
    space = _query_time_space(fixed=True)
    space_path.write_text(
        yaml.safe_dump(
            {
                "base_config": "does/not/exist.yaml",
                "study_name": "base_config_override",
                "storage_uri": "sqlite:///:memory:",
                "n_trials": 1,
                "retrieval_only": True,
                "limit": 2,
                "seed": 0,
                "search_space": space.search_space.model_dump(mode="json"),
            }
        )
    )
    # A row without query_id: this path only fails after the base config resolved.
    bad_dataset = tmp_path / "bad_dev.jsonl"
    bad_dataset.write_text('{"doc_id": "doc_de_001"}\n', encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "hpo",
            "--config",
            str(space_path),
            "--base-config",
            str(champion_path),
            "--dataset",
            str(bad_dataset),
            "--retrieval-only",
            "--n-trials",
            "1",
            "--json",
        ],
    )

    assert result.exit_code != EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert "does/not/exist.yaml" not in payload["error"]
    assert "query_id" in payload["error"]


@pytest.mark.cli
def test_a_missing_base_config_names_the_file(cli_runner: CliRunner, tmp_path: Path) -> None:
    space_path = tmp_path / "space.yaml"
    space_path.write_text(yaml.safe_dump({"base_config": "configs/default.yaml", "n_trials": 1}))
    result = cli_runner.invoke(
        app,
        [
            "hpo",
            "--config",
            str(space_path),
            "--base-config",
            str(tmp_path / "missing.yaml"),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR, result.output
    payload = json.loads(result.stdout)
    assert "missing.yaml" in payload["error"]


def test_an_explicit_index_dir_is_filled_from_the_remote_base(
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """--index-dir that a test wrote an index into is reused without a copy."""
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    explicit = run_dir / "my-idx"

    config = RAGConfig()
    config.retrieval.lancedb_index_dir = "s3://deepfix-index/optimization/runs/abc/input"
    opt = HyperparameterOpt(
        base_config=config,
        hpo_config=_query_time_space(),
        study_name="explicit_dir_test",
        run_dir=run_dir,
        index_dir=explicit,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        persist=False,
    )
    copy_index_tree(str(index_dir), explicit)

    # A populated explicit directory is used as-is: no remote fetch is attempted,
    # and the overrides point every trial at it.
    assert opt.local_index_for() == explicit
    assert opt.index_copy_source is None
    assert opt._base_overrides()["retrieval.lancedb_index_dir"] == str(explicit)


@pytest.mark.cli
def test_hpo_json_stdout_is_one_json_document(
    cli_runner: CliRunner,
    sample_corpus_path: Path,
    tmp_path: Path,
) -> None:
    """MLflow's "View run" chatter must not land on the agent's stdout."""
    index_dir = _fixture_index(sample_corpus_path, tmp_path)
    champion_path = tmp_path / "champion.yaml"
    champion_path.write_text(_base_config(index_dir, sample_corpus_path).to_yaml())
    space_path = tmp_path / "space.yaml"
    space = _query_time_space(fixed=True)
    space_path.write_text(
        yaml.safe_dump(
            {
                "base_config": str(champion_path),
                "study_name": "stdout_test",
                "storage_uri": "sqlite:///:memory:",
                "n_trials": 1,
                "retrieval_only": True,
                "limit": 2,
                "seed": 0,
                "search_space": space.search_space.model_dump(mode="json"),
            }
        )
    )
    result = cli_runner.invoke(
        app,
        [
            "hpo",
            "--config",
            str(space_path),
            "--dataset",
            str(tmp_path / "qa.jsonl"),
            "--retrieval-only",
            "--n-trials",
            "1",
            "--json",
        ],
    )
    # Even the error payload is a single JSON object: no MLflow line precedes it.
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "View run" not in result.stdout


@pytest.mark.unit
def test_a_relative_storage_uri_lands_in_the_run_directory() -> None:
    """The shipped `sqlite:///hpo.db` must not drop a database in the workspace root."""
    from deepfix_rag.cli.hpo import _resolve_storage_uri

    run_dir = Path("/tmp/runs/abc")
    assert _resolve_storage_uri("sqlite:///hpo.db", run_dir) == "sqlite:////tmp/runs/abc/hpo.db"
    assert _resolve_storage_uri("sqlite:///hpo.db", None) == "sqlite:///hpo.db"
    # An absolute file, an in-memory study and a non-sqlite URI are left alone.
    assert (
        _resolve_storage_uri("sqlite:////var/tmp/hpo.db", run_dir) == "sqlite:////var/tmp/hpo.db"
    )
    assert _resolve_storage_uri("sqlite:///:memory:", run_dir) == "sqlite:///:memory:"
    assert _resolve_storage_uri("postgresql://host/db", run_dir) == "postgresql://host/db"


def test_the_shipped_space_is_file_backed() -> None:
    """A study that cannot resume loses every trial on a restart."""
    from deepfix_rag.config.hpo import load_hpo_config

    config = load_hpo_config(
        Path(__file__).resolve().parents[1] / "configs" / "hpo_retrieval_small.yaml"
    )
    assert config.storage_uri != "sqlite:///:memory:"
    assert config.storage_uri.startswith("sqlite:///")

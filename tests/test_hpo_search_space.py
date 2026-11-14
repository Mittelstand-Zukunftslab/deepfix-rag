"""The shipped small retrieval space only samples parameters that change ranking.

Every trial is generated against an in-memory Optuna study, so the assertions
are about what the search space can emit, not about scores.
"""

from pathlib import Path

import optuna
import pytest
import yaml

from deepfix_rag.config.hpo import (
    HPOConfig,
    HybridSearchSpace,
    load_hpo_config,
)
from deepfix_rag.config.schema import RAGConfig

pytestmark = [pytest.mark.unit]

SMALL_PATH = Path(__file__).resolve().parents[1] / "configs" / "hpo_retrieval_small.yaml"
TRIALS = 200
WEIGHT_MODES = {"relative_score", "dist_based_score"}
NO_WEIGHT_MODES = {"reciprocal_rerank", "simple"}


def _small() -> HPOConfig:
    return load_hpo_config(SMALL_PATH)


def _samples(space: HybridSearchSpace, trials: int = TRIALS) -> list[dict]:
    """Every parameter set the space can produce, sampled into a throwaway study.

    The returned mapping is what HPO applies to the base config, so it includes
    derived keys such as ``retrieval.bm25_weight`` and a rerank that was turned
    off without suggesting the flag.
    """
    seen: list[dict] = []

    def objective(trial: optuna.Trial) -> float:
        seen.append(space.suggest_hybrid(trial, tune_chunking=False))
        return 0.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=trials)
    return seen


def test_the_shipped_small_space_declares_what_the_prd_specifies() -> None:
    config = _small()
    assert config.retrieval_only is True
    assert config.tune_chunking is False
    assert config.n_trials == 12
    assert config.limit == 20
    assert config.seed == 0
    assert config.timeout is not None and config.timeout > 0
    assert config.base_config is not None

    hybrid = config.search_space.hybrid
    assert hybrid.query_expansion_num_min == 1
    assert hybrid.query_expansion_num_max == 1
    assert hybrid.top_k_choices == [10, 20, 50]
    assert hybrid.rerank_choices == [False, True]
    assert hybrid.rerank_top_n_choices == [5, 10]
    assert [mode.value for mode in hybrid.fusion_modes] == [
        "reciprocal_rerank",
        "relative_score",
        "dist_based_score",
    ]
    assert hybrid.dense_weight_choices == [0.3, 0.5, 0.7]
    assert hybrid.weights_only_with_score_fusion is True
    assert hybrid.tune_bm25_params is False


def test_the_shipped_small_space_never_expands_a_query() -> None:
    for params in _samples(_small().search_space.hybrid):
        assert params["retrieval.query_expansion_num"] == 1


def test_the_shipped_small_space_never_samples_bm25_saturation() -> None:
    for params in _samples(_small().search_space.hybrid):
        assert "retrieval.bm25_k1" not in params
        assert "retrieval.bm25_b" not in params


def test_the_shipped_small_space_never_samples_chunking() -> None:
    params_seen = _samples(_small().search_space.hybrid)
    for params in params_seen:
        assert not any(key.startswith("chunking.") for key in params)


def test_weights_appear_only_under_a_fusion_mode_that_reads_them() -> None:
    for params in _samples(_small().search_space.hybrid):
        mode = params["retrieval.fusion_mode"]
        if mode in NO_WEIGHT_MODES:
            assert "retrieval.dense_weight" not in params
            assert "retrieval.bm25_weight" not in params
        else:
            assert mode in WEIGHT_MODES
            assert params["retrieval.dense_weight"] in (0.3, 0.5, 0.7)
            assert (
                round(params["retrieval.dense_weight"] + params["retrieval.bm25_weight"], 4) == 1.0
            )


def test_the_shipped_space_never_samples_the_simple_fusion_mode() -> None:
    for params in _samples(_small().search_space.hybrid, trials=60):
        assert params["retrieval.fusion_mode"] != "simple"


def test_an_enabled_rerank_always_fits_the_pool_and_the_cutoff() -> None:
    """`rerank.top_n <= top_k` and `eval.cutoff_k <= rerank.top_n` must hold."""
    cutoff = RAGConfig().eval.cutoff_k
    seen_enabled = 0
    for params in _samples(_small().search_space.hybrid):
        if not params["rerank.enabled"]:
            assert "rerank.top_n" not in params
            continue
        seen_enabled += 1
        assert params["rerank.top_n"] <= params["retrieval.top_k"]
        assert params["rerank.top_n"] >= cutoff
    assert seen_enabled > 0


def test_every_sample_builds_a_valid_rag_config() -> None:
    """No trial may suggest a combination the RAGConfig validator rejects."""
    base = RAGConfig()
    space = _small().search_space.hybrid
    for params in _samples(space):
        config = base.model_copy(deep=True)
        for key, value in params.items():
            target = config
            parts = key.split(".")
            for part in parts[:-1]:
                target = getattr(target, part)
            setattr(target, parts[-1], value)
        if config.rerank.enabled:
            assert config.rerank.top_n <= config.retrieval.top_k
            assert config.eval.cutoff_k <= config.rerank.top_n


def test_a_top_n_that_cannot_fit_turns_rerank_off() -> None:
    """A pool smaller than every top_n candidate does not emit an invalid config."""
    space = HybridSearchSpace.model_validate(
        {
            "top_k_choices": [3],
            "rerank_choices": [True],
            "rerank_top_n_choices": [5, 10],
            "tune_bm25_params": False,
            "fusion_modes": ["reciprocal_rerank"],
        }
    )
    for params in _samples(space, trials=5):
        assert params["rerank.enabled"] is False
        assert "rerank.top_n" not in params


def test_a_space_without_rerank_choices_leaves_rerank_alone() -> None:
    space = HybridSearchSpace.model_validate({"top_k_choices": [10], "tune_bm25_params": False})
    for params in _samples(space, trials=5):
        assert not any(key.startswith("rerank.") for key in params)


def test_the_broader_manual_space_still_samples_the_wider_bounds() -> None:
    """configs/hpo.yaml keeps its manual-use bounds, including the BM25 parameters."""
    from deepfix_rag.config.mutator import apply_mutation

    config = load_hpo_config(Path(__file__).resolve().parents[1] / "configs" / "hpo.yaml")
    hybrid = config.search_space.hybrid
    assert hybrid.tune_bm25_params is True
    assert hybrid.query_expansion_num_max == 5
    samples = _samples(hybrid, trials=20)
    assert any("retrieval.bm25_k1" in params for params in samples)
    assert any(params["retrieval.query_expansion_num"] > 1 for params in samples)

    base = RAGConfig()
    assert apply_mutation(base, samples[0]) is not None


def test_the_shipped_yaml_names_only_real_space_keys() -> None:
    loaded = yaml.safe_load(SMALL_PATH.read_text(encoding="utf-8"))
    hybrid = loaded["search_space"]["hybrid"]
    assert hybrid["tune_bm25_params"] is False
    assert "bm25_k1_min" not in hybrid
    assert "bm25_k1_max" not in hybrid
    assert "strategies" not in hybrid
    assert "chunk_size_min" not in hybrid

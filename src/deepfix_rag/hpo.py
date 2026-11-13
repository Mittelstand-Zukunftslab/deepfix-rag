"""Hyperparameter Optimization (HPO) engine powered by Optuna."""

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import mlflow
import optuna
from optuna.trial import FrozenTrial, TrialState
import pandas as pd
import yaml

from deepfix_rag.config.hpo import (
    HPOConfig,
    HPOSearchSpace,
    resolve_metric_candidates,
)
from deepfix_rag.config.mutator import apply_mutation
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.evaluation.runner import EvaluationRunner
from deepfix_rag.index_cache import (
    index_dir_populated as _index_dir_populated,
)
from deepfix_rag.index_cache import (
    is_remote_index_uri,
    localize_index,
)

INDEX_CACHE_DIRNAME = "idx-cache"
TRIALS_ARTIFACT_NAME = "hpo_trials.json"
# Overrides that exist only so a trial can score a subsample. They must never
# reach an exported candidate, which is re-scored on the full dev set.
_TRIAL_ONLY_OVERRIDE_KEYS = frozenset(
    {
        "data.path_or_repo",
        "data.limit",
        "data.split",
        "retrieval.lancedb_index_dir",
    }
)


def write_best_so_far(path: Path | str, config: RAGConfig) -> Path:
    """Write the best trial's config so a stopped study still leaves a candidate."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return out


def _trial_row(trial: FrozenTrial, metric_names: list[str]) -> dict[str, Any]:
    values = list(trial.values) if trial.values is not None else []
    if not values and trial.value is not None:
        values = [trial.value]
    return {
        "trial_number": trial.number,
        "state": trial.state.name,
        "params": dict(trial.params),
        "values": values,
        "metrics": {
            name: values[index] for index, name in enumerate(metric_names) if index < len(values)
        },
        "user_attrs": dict(trial.user_attrs),
        "duration_seconds": trial.duration.total_seconds() if trial.duration else None,
    }


def _write_trial_rows(
    run_dir: Path | str,
    metric_names: list[str],
    trials: list[FrozenTrial],
) -> Path:
    """Rewrite ``<run_dir>/hpo_trials.json`` with every trial seen so far."""
    target = Path(run_dir) / TRIALS_ARTIFACT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [_trial_row(trial, metric_names) for trial in trials]
    target.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return target


def _best_trial(study: optuna.Study) -> FrozenTrial | None:
    complete = [trial for trial in study.trials if trial.state == TrialState.COMPLETE]
    if not complete:
        return None

    def score(trial: FrozenTrial) -> float:
        values = list(trial.values or [])
        if values:
            return sum(float(value) for value in values)
        return float(trial.value or 0.0)

    return max(complete, key=score)


def index_cache_path(run_dir: Path | str) -> Path:
    """Default local cache for a remote index under the run directory."""
    return Path(run_dir) / INDEX_CACHE_DIRNAME


@dataclass
class HPOResult:
    """Results from an Optuna Hyperparameter Optimization study."""

    study: optuna.Study
    base_config: RAGConfig
    metric_names: list[str]
    pareto_trials: list[FrozenTrial] = field(default_factory=list)
    pareto_configs: list[RAGConfig] = field(default_factory=list)
    best_trial: FrozenTrial | None = None
    best_config: RAGConfig | None = None
    run_dir: Path | None = None
    trials_path: Path | None = None
    index_dir: Path | None = None
    index_cache_source: str | None = None
    stopped_early: bool = False
    failed_trials: int = 0
    pruned_trials: int = 0

    def to_dataframe(self) -> pd.DataFrame:
        """Convert study trials to a structured DataFrame."""
        records: list[dict[str, Any]] = []
        for t in self.study.trials:
            if t.state != TrialState.COMPLETE:
                continue
            rec: dict[str, Any] = {
                "trial_number": t.number,
                "duration_seconds": t.duration.total_seconds() if t.duration else 0.0,
            }
            if t.values is not None:
                for idx, m_name in enumerate(self.metric_names):
                    rec[m_name] = t.values[idx] if idx < len(t.values) else None
            elif t.value is not None and self.metric_names:
                rec[self.metric_names[0]] = t.value

            rec.update(t.params)
            for attr_k, attr_v in t.user_attrs.items():
                rec[f"user_attr.{attr_k}"] = attr_v
            records.append(rec)
        return pd.DataFrame(records)

    def to_dict(self) -> dict[str, Any]:
        """Convert HPO outcome to a JSON-serializable dictionary."""
        best_trial_dict: dict[str, Any] | None = None
        if self.best_trial is not None:
            best_trial_dict = {
                "trial_number": self.best_trial.number,
                "params": self.best_trial.params,
                "values": self.best_trial.values
                or ([self.best_trial.value] if self.best_trial.value is not None else []),
                "user_attrs": self.best_trial.user_attrs,
            }

        pareto_list: list[dict[str, Any]] = []
        for pt in self.pareto_trials:
            pareto_list.append(
                {
                    "trial_number": pt.number,
                    "params": pt.params,
                    "values": pt.values or ([pt.value] if pt.value is not None else []),
                }
            )

        return {
            "study_name": self.study.study_name,
            "metric_names": self.metric_names,
            "total_trials": len(self.study.trials),
            "completed_trials": len(
                [t for t in self.study.trials if t.state == TrialState.COMPLETE]
            ),
            "best_trial": best_trial_dict,
            "pareto_trials": pareto_list,
            "best_config": self.best_config.model_dump() if self.best_config else None,
            "stopped_early": self.stopped_early,
            "failed_trials": self.failed_trials,
            "pruned_trials": self.pruned_trials,
            "run_dir": str(self.run_dir) if self.run_dir is not None else None,
            "trials_path": str(self.trials_path) if self.trials_path is not None else None,
            "index_dir": str(self.index_dir) if self.index_dir is not None else None,
            "index_cache_source": self.index_cache_source,
        }

    def export_best_config(self, output_path: Path | str) -> Path:
        """Export the best configuration to a YAML file."""
        if self.best_config is None:
            raise ValueError("Cannot export configuration: no best trial found.")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            yaml.safe_dump(self.best_config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        return out

    def log_to_mlflow(self, run_name: str | None = None) -> None:
        """Log HPO study summary and best parameters to MLflow."""
        active_run = mlflow.active_run()
        ctx_manager = (
            mlflow.start_run(run_name=run_name or f"hpo_{self.study.study_name}", nested=True)
            if active_run is not None
            else mlflow.start_run(run_name=run_name or f"hpo_{self.study.study_name}")
        )
        with ctx_manager:
            if self.best_trial:
                mlflow.log_params({f"best.{k}": v for k, v in self.best_trial.params.items()})
                if self.best_trial.values:
                    for m_name, val in zip(self.metric_names, self.best_trial.values):
                        mlflow.log_metric(f"best.{m_name}", float(val))
                elif self.best_trial.value is not None and self.metric_names:
                    mlflow.log_metric(f"best.{self.metric_names[0]}", float(self.best_trial.value))

            mlflow.log_metric("hpo.total_trials", len(self.study.trials))
            mlflow.log_metric("hpo.pareto_count", len(self.pareto_trials))

            df = self.to_dataframe()
            if not df.empty:
                mlflow.log_table(data=df, artifact_file="hpo_study_trials.json")


class TrialEvaluationError(RuntimeError):
    """Every case in one HPO trial errored, so the trial measured nothing.

    A retrieval-only trial whose rerank endpoint 404s or whose embedding call
    fails would otherwise report 0.0 and read as a worse configuration. That
    silently turns "the lever is broken" into "the lever does not help", which
    is the failure this feature exists to prevent. The trial is marked FAILED
    and never becomes the best trial.
    """

    def __init__(self, trial_number: int, message: str) -> None:
        self.trial_number = trial_number
        self.message = message
        super().__init__(f"trial {trial_number}: {message}")


class HyperparameterOpt:
    """Multi-paradigm Hyperparameter Optimization engine using Optuna."""

    def __init__(
        self,
        base_config: RAGConfig,
        hpo_config: HPOConfig | None = None,
        search_space: HPOSearchSpace | None = None,
        study_name: str | None = None,
        storage_uri: str = "sqlite:///:memory:",
        tune_chunking: bool = False,
        seed: int | None = None,
        run_dir: Path | str | None = None,
        index_dir: Path | str | None = None,
        output_path: Path | str | None = None,
        export_base_config: RAGConfig | None = None,
        embed_model: Any = None,
        llm: Any = None,
        persist: bool = True,
    ) -> None:
        self.base_config = base_config
        # The config an exported candidate is built from. Trials and exports
        # differ: a trial carries the CLI's dataset, split, limit and local
        # index path so it can score a subsample, but none of those belong in a
        # candidate that will be re-scored on the full dev set. Exports use this
        # config plus the sampled params, nothing else.
        self.export_base_config = export_base_config or base_config
        self.hpo_config = hpo_config or HPOConfig(search_space=search_space or HPOSearchSpace())
        if search_space is not None:
            self.hpo_config.search_space = search_space
        self.search_space = self.hpo_config.search_space
        self.study_name = (
            study_name or self.hpo_config.study_name or f"hpo_{base_config.retrieval.backend}"
        )
        effective_storage_uri = (
            storage_uri
            if storage_uri != "sqlite:///:memory:"
            else (self.hpo_config.storage_uri or storage_uri)
        )
        self.storage = optuna.storages.RDBStorage(
            url=effective_storage_uri,
            engine_kwargs={"pool_size": 20, "connect_args": {"timeout": 10}},
        )
        self.tune_chunking = tune_chunking or self.hpo_config.tune_chunking
        self.seed = seed
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.output_path = Path(output_path) if output_path is not None else None
        # An explicit --index-dir wins; otherwise a remote base index is copied
        # once to <run_dir>/idx-cache and every trial reads that local copy.
        self.index_dir = Path(index_dir) if index_dir is not None else None
        self.local_index_dir: Path | None = None
        self.index_copy_source: str | None = None
        # A test pins the embedding model so it never calls a live endpoint.
        self.embed_model = embed_model
        self.llm = llm
        self.persist = persist

        if base_config.retrieval_only:
            self.metric_names = list(self.hpo_config.metrics.retrieval_only_targets)
        else:
            self.metric_names = list(self.hpo_config.metrics.targets)

    @staticmethod
    def extract_metric(
        metrics: dict[str, float],
        name: str,
        cutoff_k: int | None = None,
        aliases_map: dict[str, list[str]] | None = None,
    ) -> float:
        """Safely extract metric score from evaluation summary dictionary using cutoff k and candidates."""
        if name in metrics and metrics[name] is not None:
            return float(metrics[name])

        # Check explicit custom aliases first if provided
        if aliases_map and name in aliases_map:
            for cand in aliases_map[name]:
                if cand in metrics and metrics[cand] is not None:
                    return float(metrics[cand])

        # Check dynamic candidates parameterized by cutoff_k
        for cand in resolve_metric_candidates(name, cutoff_k=cutoff_k):
            if cand in metrics and metrics[cand] is not None:
                return float(metrics[cand])

        return 0.0

    async def _evaluate_trial(
        self,
        trial_config: RAGConfig,
        dataset_path: Path | str,
        split: str,
        limit: int | None,
        run_name: str,
        seed: int | None = None,
    ) -> dict[str, float]:
        """Execute EvaluationRunner asynchronously for a single trial.

        A trial where every case errored measured nothing, so it raises rather
        than returning zeros: Optuna then records FAIL, the trial cannot win,
        and the error text reaches the caller and ``hpo_trials.json``.
        """
        runner = EvaluationRunner(
            config=trial_config,
            embed_model=self.embed_model,
            llm=self.llm,
            persist=self.persist,
        )
        summary = await runner.run(
            dataset_path=dataset_path,
            run_name=run_name,
            split=split,
            limit=limit,
            seed=seed,
        )
        if summary.scored and summary.errors >= summary.scored:
            first_error = summary.error_details[0].get("error") if summary.error_details else None
            raise TrialEvaluationError(
                -1,
                f"all {summary.errors} cases errored, nothing was measured. "
                f"First error: {first_error}",
            )
        return summary.metrics

    def local_index_for(self) -> Path | None:
        """The local index every trial should read, or None to keep the base URI.

        A remote ``retrieval.lancedb_index_dir`` is copied once into
        ``<run_dir>/idx-cache`` (or the explicit ``--index-dir``) before the
        first trial. Later trials in this process reuse that copy, so the
        object store is not fetched again per trial. An explicit
        ``--index-dir`` that does not yet hold the index is filled from the
        remote base, so the agent never has to copy it by hand.
        """
        if self.local_index_dir is not None:
            return self.local_index_dir
        base_uri = str(self.base_config.retrieval.lancedb_index_dir)
        remote_base = is_remote_index_uri(base_uri)
        configured = self.index_dir
        if configured is not None:
            self.local_index_dir = configured
            if remote_base and not _index_dir_populated(configured):
                LOGGER.info(f"Copying the index from {base_uri} into {configured}")
                localize_index(base_uri, configured, self.base_config)
                self.index_copy_source = base_uri
            return configured
        if not remote_base:
            return None
        self.local_index_dir = self._copy_remote_index(base_uri)
        self.index_copy_source = base_uri
        return self.local_index_dir

    def _copy_remote_index(self, uri: str) -> Path:
        """Copy a remote index into the run directory exactly once.

        The copy carries a manifest built for this run's base config, so a later
        ``deepfix eval --index-dir`` on the cache passes the staleness check
        instead of falling back to the object store.
        """
        base = self.run_dir or Path.cwd()
        target = Path(base) / INDEX_CACHE_DIRNAME
        if _index_dir_populated(target):
            LOGGER.info(f"Reusing the cached index at {target}")
            return target
        LOGGER.info(f"Copying the index from {uri} into {target}")
        localize_index(uri, target, self.base_config)
        return target

    def _base_overrides(self) -> dict[str, Any]:
        """Index location plus the sample seed shared by every trial."""
        overrides: dict[str, Any] = {}
        local = self.local_index_for()
        if local is not None:
            overrides["retrieval.lancedb_index_dir"] = str(local)
            overrides["retrieval.load_if_exists"] = True
        return overrides

    def _failed_trial_error(self, trial: optuna.Trial, message: str) -> TrialEvaluationError:
        """Build the error that marks a trial FAILED instead of scoring it 0.0."""
        return TrialEvaluationError(trial.number, message)

    def _run_trial_evaluation(
        self,
        *,
        trial_config: RAGConfig,
        dataset_path: Path | str,
        split: str,
        limit: int | None,
        run_name: str,
    ) -> dict[str, float]:
        """Run one trial's evaluation in a clean event loop."""
        kwargs = {
            "trial_config": trial_config,
            "dataset_path": dataset_path,
            "split": split,
            "limit": limit,
            "run_name": run_name,
            "seed": self.seed,
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, self._evaluate_trial(**kwargs)).result()
        return asyncio.run(self._evaluate_trial(**kwargs))

    @staticmethod
    def _duplicate_of(trial: optuna.Trial, params: dict[str, Any]) -> int | None:
        """The earlier trial with the same parameters, or None.

        Optuna's TPE sampler can redraw a parameter set it has already scored
        when the space is small and discrete, which spends a full eval for no
        information. This compares against the study's earlier trials.
        """
        study = trial.study
        for earlier in study.trials:
            if earlier.number >= trial.number:
                break
            if earlier.state != TrialState.COMPLETE:
                continue
            if dict(earlier.params) == dict(params):
                return earlier.number
        return None

    def _objective(
        self,
        trial: optuna.Trial,
        dataset_path: Path | str,
        split: str,
        limit: int | None,
    ) -> tuple[float, ...]:
        """Optuna objective function."""
        # 1. Sample overrides according to paradigm
        paradigm = self.base_config.retrieval.backend
        overrides = self.search_space.suggest_parameters(
            trial=trial, paradigm=paradigm, tune_chunking=self.tune_chunking
        )

        # 2. Mutate config
        trial_config = apply_mutation(self.base_config, {**self._base_overrides(), **overrides})

        # 2b. A small discrete space can sample a param set the study already
        # scored. Pruning it costs no eval and keeps the trial budget for
        # unexplored configurations.
        duplicate = self._duplicate_of(trial, overrides)
        if duplicate is not None:
            trial.set_user_attr("duplicate_of", duplicate)
            LOGGER.info(
                f"Pruning trial {trial.number}: it repeats trial {duplicate}'s parameters."
            )
            raise optuna.TrialPruned(f"duplicate of trial {duplicate}")

        temp_dir: str | None = None
        # 3. Check if chunking was modified and isolate index dir if so
        if self.tune_chunking and any(k.startswith("chunking.") for k in overrides):
            temp_dir = tempfile.mkdtemp(prefix=f"deepfix_hpo_trial_{trial.number}_")
            trial_config = apply_mutation(
                trial_config,
                {
                    "retrieval.lancedb_index_dir": temp_dir,
                    "retrieval.load_if_exists": False,
                },
            )

        run_name = f"hpo_{self.study_name}_trial_{trial.number}"
        try:
            try:
                metrics = self._run_trial_evaluation(
                    trial_config=trial_config,
                    dataset_path=dataset_path,
                    split=split,
                    limit=limit,
                    run_name=run_name,
                )
            except TrialEvaluationError as exc:
                error = TrialEvaluationError(trial.number, exc.message)
                trial.set_user_attr("error", error.message)
                raise error from exc

            cutoff_k = trial_config.eval.cutoff_k or trial_config.retrieval.top_k
            # 4. Extract scores for target metrics
            scores: list[float] = [
                self.extract_metric(
                    metrics,
                    m,
                    cutoff_k=cutoff_k,
                    aliases_map=self.hpo_config.metrics.aliases,
                )
                for m in self.metric_names
            ]

            # Save diagnostic info to trial attributes
            for k in ("retrieval_induced_error_rate", "deflection_rate"):
                if k in metrics:
                    trial.set_user_attr(k, float(metrics[k]))

            return tuple(scores)

        finally:
            if temp_dir is not None and Path(temp_dir).exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    LOGGER.debug(f"Failed to cleanup temp dir {temp_dir}: {e}")

    def _export_overrides(self, params: dict[str, Any]) -> dict[str, Any]:
        """Overrides for an exported candidate: the tuned params and nothing else.

        ``retrieval.lancedb_index_dir`` and ``data.path_or_repo`` never travel
        into a candidate. A trial points them at the local index cache and the
        CLI's dataset so it can score a subsample; the candidate is re-scored by
        ``deepfix eval`` on the index and dataset its own config names, and a
        ``<run_dir>/idx-cache`` path does not exist on the API host.
        """
        return {
            key: value for key, value in params.items() if key not in _TRIAL_ONLY_OVERRIDE_KEYS
        }

    def _export_config_for(self, params: dict[str, Any]) -> RAGConfig:
        """The base config plus the tuned params, with no trial-only overrides."""
        return apply_mutation(self.export_base_config, self._export_overrides(params))

    def _best_config_from_trial(self, trial: FrozenTrial) -> RAGConfig:
        """The exported candidate for one trial."""
        return self._export_config_for(dict(trial.params))

    def _record_trial(self, study: optuna.Study, trial: FrozenTrial) -> None:
        """Write best-so-far artifacts after each finished trial.

        A ``--timeout`` or a crash after at least one complete trial leaves
        ``hpo_trials.json`` and the best-so-far YAML behind, so a wrapped-up
        round still has something to confirm on the full dev set.
        """
        if self.run_dir is None:
            return
        try:
            _write_trial_rows(self.run_dir, self.metric_names, study.trials)
            best = _best_trial(study)
            if best is not None:
                config = self._best_config_from_trial(best)
                target = self.output_path or (self.run_dir / "candidate.yaml")
                write_best_so_far(target, config)
        except Exception as exc:  # a failed artifact must not kill the study
            LOGGER.warning(f"Could not refresh the HPO artifacts: {exc}")

    def optimize(
        self,
        n_trials: int | None = None,
        timeout: float | None = None,
        dataset_path: Path | str | None = None,
        split: str | None = None,
        limit: int | None = None,
        workspace: str | None = None,
        load_if_exists: bool = True,
    ) -> HPOResult:
        """Run the Optuna optimization study."""
        target_n_trials = n_trials if n_trials is not None else self.hpo_config.n_trials
        target_dataset = (
            dataset_path or self.hpo_config.dataset or self.base_config.data.path_or_repo
        )
        target_split = split or self.hpo_config.split or self.base_config.data.split or "train"
        target_limit = (
            limit if limit is not None else (self.hpo_config.limit or self.base_config.data.limit)
        )
        # Copy a remote index before the first trial so no trial fetches it again.
        self.local_index_for()

        directions = ["maximize"] * len(self.metric_names)

        study = optuna.create_study(
            study_name=self.study_name,
            storage=self.storage,
            directions=directions,
            load_if_exists=load_if_exists,
        )

        # n_trials is the number of *evaluated* trials the study should hold.
        # A pruned duplicate costs no evaluation, so the loop keeps going until
        # enough trials completed or the wall clock runs out.
        deadline = None if timeout is None else time.monotonic() + timeout
        max_attempts = target_n_trials + max(target_n_trials * 4, 20)
        while True:
            completed_count = len([t for t in study.trials if t.state == TrialState.COMPLETE])
            if completed_count >= target_n_trials:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            if len(study.trials) >= max_attempts:
                LOGGER.info(
                    f"Stopping after {len(study.trials)} attempts: the search space has "
                    f"no configuration left to try ({completed_count} completed)."
                )
                break
            before = len(study.trials)
            study.optimize(
                lambda trial: self._objective(
                    trial=trial,
                    dataset_path=target_dataset,
                    split=target_split,
                    limit=target_limit,
                ),
                n_trials=1,
                timeout=(None if deadline is None else max(1e-6, deadline - time.monotonic())),
                callbacks=[self._record_trial],
                # A trial whose cases all errored measures nothing. Record it
                # FAILED and keep sweeping instead of aborting the whole study,
                # then fail loudly below if nothing ever completed.
                catch=(TrialEvaluationError,),
            )
            if len(study.trials) == before:
                # The timeout expired before Optuna could start a trial.
                break

        completed_trials = [t for t in study.trials if t.state == TrialState.COMPLETE]
        if not completed_trials:
            failures = [t for t in study.trials if t.state == TrialState.FAIL]
            if failures:
                reason = failures[0].user_attrs.get("error")
                raise TrialEvaluationError(
                    0,
                    "no trial completed, so nothing was measured"
                    + (f". {reason}" if reason else "."),
                )

        # Build Pareto trials and configurations
        pareto_trials: list[FrozenTrial] = []
        pareto_configs: list[RAGConfig] = []

        if len(directions) > 1:
            try:
                pareto_trials = list(study.best_trials)
            except Exception:
                pareto_trials = []
        else:
            try:
                pareto_trials = [study.best_trial]
            except Exception:
                pareto_trials = []

        for pt in pareto_trials:
            p_config = self._export_config_for(dict(pt.params))
            pareto_configs.append(p_config)

        # Select representative best trial
        best_trial: FrozenTrial | None = None
        best_config: RAGConfig | None = None

        if pareto_trials:
            # Rank by sum of objective values
            best_trial = max(
                pareto_trials,
                key=lambda t: sum(t.values) if t.values else (t.value or 0.0),
            )
            idx = pareto_trials.index(best_trial)
            best_config = pareto_configs[idx]

        final_completed = len([t for t in study.trials if t.state == TrialState.COMPLETE])
        failed_count = len([t for t in study.trials if t.state == TrialState.FAIL])
        pruned_count = len([t for t in study.trials if t.state == TrialState.PRUNED])
        trials_path: Path | None = None
        if self.run_dir is not None:
            trials_path = _write_trial_rows(self.run_dir, self.metric_names, study.trials)

        return HPOResult(
            study=study,
            base_config=self.base_config,
            metric_names=self.metric_names,
            pareto_trials=pareto_trials,
            pareto_configs=pareto_configs,
            best_trial=best_trial,
            best_config=best_config,
            run_dir=self.run_dir,
            trials_path=trials_path,
            index_dir=self.local_index_dir,
            index_cache_source=self.index_copy_source,
            failed_trials=failed_count,
            # A timeout, or a trial that failed, can leave the budget unspent.
            pruned_trials=pruned_count,
            stopped_early=final_completed < target_n_trials,
        )

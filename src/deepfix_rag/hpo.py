"""Hyperparameter Optimization (HPO) engine powered by Optuna."""

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import tempfile
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
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.evaluation.runner import EvaluationRunner

INDEX_CACHE_DIRNAME = "idx-cache"
TRIALS_ARTIFACT_NAME = "hpo_trials.json"
_REMOTE_SCHEMES = ("s3://", "gs://", "az://", "abfs://", "abfss://", "hdfs://", "gcs://")


def is_remote_index_uri(uri: str) -> bool:
    """True for an index outside the local filesystem."""
    return str(uri).strip().lower().startswith(_REMOTE_SCHEMES)


def copy_index_tree(source: str, target: Path) -> Path:
    """Copy a directory or a remote object prefix into ``target``.

    A local directory is copied with ``copytree``. A remote ``s3://bucket/key``
    prefix is walked with pyarrow's S3 filesystem, which reads the same
    ``S3_*`` settings LanceDB already uses. No full-object download is repeated
    once the files are on disk.
    """
    target.mkdir(parents=True, exist_ok=True)
    if not is_remote_index_uri(source):
        source_path = Path(source)
        if not source_path.is_dir():
            raise FileNotFoundError(f"Index directory does not exist: {source}")
        shutil.copytree(source_path, target, dirs_exist_ok=True)
        return target
    _copy_object_prefix(source, target)
    return target


def _copy_object_prefix(uri: str, target: Path) -> None:
    from pyarrow import fs

    bucket, _, prefix = uri[len("s3://") :].partition("/")
    filesystem = fs.S3FileSystem(
        endpoint_override=settings.S3_ENDPOINT_URL or None,
        access_key=settings.S3_ACCESS_KEY_ID or None,
        secret_key=settings.S3_SECRET_ACCESS_KEY or None,
        region=settings.S3_REGION or None,
        scheme="http" if str(settings.S3_ENDPOINT_URL or "").startswith("http://") else "https",
    )
    selector = fs.FileSelector(prefix.rstrip("/"), recursive=True)
    infos = filesystem.get_file_info(selector)
    copied = 0
    for info in infos:
        if info.type != fs.FileType.File:
            continue
        relative = info.path[len(prefix.rstrip("/")) :].lstrip("/")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with filesystem.open_input_stream(info.path) as reader:
            destination.write_bytes(reader.read())
        copied += 1
    if copied == 0:
        raise FileNotFoundError(f"No index files found under {uri}")


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
        embed_model: Any = None,
        llm: Any = None,
        persist: bool = True,
    ) -> None:
        self.base_config = base_config
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
        """Execute EvaluationRunner asynchronously for a single trial."""
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
        return summary.metrics

    def local_index_for(self) -> Path | None:
        """The local index every trial should read, or None to keep the base URI.

        A remote ``retrieval.lancedb_index_dir`` is copied once into
        ``<run_dir>/idx-cache`` (or the explicit ``--index-dir``) before the
        first trial. Later trials in this process reuse that copy, so the
        object store is not fetched again per trial.
        """
        configured = self.index_dir
        if configured is not None:
            return configured
        if self.local_index_dir is not None:
            return self.local_index_dir
        base_uri = str(self.base_config.retrieval.lancedb_index_dir)
        if not is_remote_index_uri(base_uri):
            return None
        self.local_index_dir = self._copy_remote_index(base_uri)
        self.index_copy_source = base_uri
        return self.local_index_dir

    def _copy_remote_index(self, uri: str) -> Path:
        """Copy a remote index into the run directory exactly once."""
        base = self.run_dir or Path.cwd()
        target = Path(base) / INDEX_CACHE_DIRNAME
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            LOGGER.info(f"Reusing the cached index at {target}")
            return target
        LOGGER.info(f"Copying the index from {uri} into {target}")
        copy_index_tree(uri, target)
        return target

    def _base_overrides(self) -> dict[str, Any]:
        """Index location plus the sample seed shared by every trial."""
        overrides: dict[str, Any] = {}
        local = self.local_index_for()
        if local is not None:
            overrides["retrieval.lancedb_index_dir"] = str(local)
            overrides["retrieval.load_if_exists"] = True
        return overrides

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
            # Run async evaluation in clean loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    metrics = executor.submit(
                        asyncio.run,
                        self._evaluate_trial(
                            trial_config=trial_config,
                            dataset_path=dataset_path,
                            split=split,
                            limit=limit,
                            run_name=run_name,
                            seed=self.seed,
                        ),
                    ).result()
            else:
                metrics = asyncio.run(
                    self._evaluate_trial(
                        trial_config=trial_config,
                        dataset_path=dataset_path,
                        split=split,
                        limit=limit,
                        run_name=run_name,
                        seed=self.seed,
                    )
                )

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

    def _best_config_from_trial(self, trial: FrozenTrial) -> RAGConfig:
        """The base config with one trial's sampled parameters applied."""
        return apply_mutation(self.base_config, {**self._base_overrides(), **dict(trial.params)})

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

        # n_trials is the study's total budget, not this call's. A file-backed
        # study that is restarted with the same --n-trials therefore continues
        # where it stopped instead of running the whole budget again.
        already_run = len(study.trials)
        remaining = max(0, target_n_trials - already_run)
        if remaining == 0:
            LOGGER.info(
                f"Study {self.study_name} already has {already_run} trials; nothing to run."
            )
        study.optimize(
            lambda trial: self._objective(
                trial=trial,
                dataset_path=target_dataset,
                split=target_split,
                limit=target_limit,
            ),
            n_trials=remaining,
            timeout=timeout,
            callbacks=[self._record_trial],
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
            param_overrides: dict[str, Any] = {}
            for param_key, param_val in pt.params.items():
                param_overrides[param_key] = param_val
            p_config = apply_mutation(
                self.base_config, {**self._base_overrides(), **param_overrides}
            )
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

        completed = len([t for t in study.trials if t.state == TrialState.COMPLETE])
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
            # A timeout can stop the study before the requested trial count.
            stopped_early=timeout is not None and completed < target_n_trials,
        )

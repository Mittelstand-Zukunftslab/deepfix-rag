"""Hyperparameter Optimization (HPO) engine powered by Optuna."""

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
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
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.evaluation.runner import EvaluationRunner


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
    ) -> dict[str, float]:
        """Execute EvaluationRunner asynchronously for a single trial."""
        runner = EvaluationRunner(config=trial_config)
        summary = await runner.run(
            dataset_path=dataset_path,
            run_name=run_name,
            split=split,
            limit=limit,
        )
        return summary.metrics

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
        trial_config = apply_mutation(self.base_config, overrides)

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

        directions = ["maximize"] * len(self.metric_names)

        study = optuna.create_study(
            study_name=self.study_name,
            storage=self.storage,
            directions=directions,
            load_if_exists=load_if_exists,
        )

        study.optimize(
            lambda trial: self._objective(
                trial=trial,
                dataset_path=target_dataset,
                split=target_split,
                limit=target_limit,
            ),
            n_trials=target_n_trials,
            timeout=timeout,
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
            p_config = apply_mutation(self.base_config, param_overrides)
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

        return HPOResult(
            study=study,
            base_config=self.base_config,
            metric_names=self.metric_names,
            pareto_trials=pareto_trials,
            pareto_configs=pareto_configs,
            best_trial=best_trial,
            best_config=best_config,
        )

"""deepfix hpo subcommand for multi-paradigm hyperparameter optimization."""

from pathlib import Path
from typing import Any

import typer

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_info,
    print_json,
    print_success,
)
from deepfix_rag.config.hpo import load_hpo_config
from deepfix_rag.config.mutator import apply_mutation
from deepfix_rag.config.schema import RetrievalBackend
from deepfix_rag.hpo import HyperparameterOpt


def hpo_cmd(
    config: Path = typer.Option(
        Path("configs/hpo.yaml"),
        "--config",
        "-c",
        help="Path to HPO configuration YAML file.",
    ),
    n_trials: int | None = typer.Option(
        None,
        "--n-trials",
        "-n",
        help="Number of Optuna optimization trials to execute.",
    ),
    storage_uri: str | None = typer.Option(
        None,
        "--storage-uri",
        help="Storage URI for Optuna study (e.g. sqlite:///hpo.db).",
    ),
    paradigm: str | None = typer.Option(
        None,
        "--paradigm",
        "-p",
        help="Override retrieval backend paradigm (dense, hybrid, graph).",
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Dataset path or HF repo ID (overrides config.data.path_or_repo).",
    ),
    split: str | None = typer.Option(
        None,
        "--split",
        "-s",
        help="Dataset split (overrides config.data.split).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-l",
        help="Maximum number of QA records per evaluation trial.",
    ),
    retrieval_only: bool = typer.Option(
        False,
        "--retrieval-only",
        help="Optimize retrieval-only metrics (NDCG and Recall) skipping LLM generation evaluation.",
    ),
    tune_chunking: bool = typer.Option(
        False,
        "--tune-chunking",
        help=(
            "Sample and optimize chunking strategy and strategy-specific hyperparameters "
            "(size, overlap, semantic buffer/percentile) across trials."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional destination path to export the best configuration YAML.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output structured JSON payload to stdout.",
    ),
) -> None:
    """Execute multi-paradigm hyperparameter optimization study using Optuna."""
    # 1. Validate and load HPO and base configuration
    if not config.exists():
        emit_error(
            f"Configuration file not found: {config}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        hpo_config = load_hpo_config(config)
        base_config = hpo_config.resolve_base_config(base_dir=config.parent)
    except Exception as e:
        emit_error(
            f"Failed to parse configuration {config}: {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    # 2. Compute effective settings and CLI overrides
    effective_n_trials = n_trials if n_trials is not None else hpo_config.n_trials
    effective_storage_uri = storage_uri or hpo_config.storage_uri
    effective_tune_chunking = tune_chunking or hpo_config.tune_chunking
    effective_retrieval_only = (
        retrieval_only or hpo_config.retrieval_only or base_config.retrieval_only
    )
    effective_dataset = dataset or hpo_config.dataset or base_config.data.path_or_repo
    effective_split = split or hpo_config.split or base_config.data.split
    effective_limit = limit if limit is not None else (hpo_config.limit or base_config.data.limit)
    effective_output = output or hpo_config.output

    overrides: dict[str, Any] = {}
    if paradigm is not None:
        try:
            overrides["retrieval.backend"] = RetrievalBackend(paradigm.lower())
        except ValueError:
            valid = [b.value for b in RetrievalBackend]
            emit_error(
                f"Invalid paradigm '{paradigm}'. Must be one of: {valid}",
                code=EXIT_VALIDATION_ERROR,
                json_output=json_output,
            )

    if effective_dataset != base_config.data.path_or_repo:
        overrides["data.path_or_repo"] = effective_dataset
    if effective_split != base_config.data.split:
        overrides["data.split"] = effective_split
    if effective_limit != base_config.data.limit:
        overrides["data.limit"] = effective_limit
    if effective_retrieval_only != base_config.retrieval_only:
        overrides["retrieval_only"] = effective_retrieval_only

    if overrides:
        try:
            base_config = apply_mutation(base_config, overrides)
        except Exception as e:
            emit_error(
                f"Failed to apply CLI overrides: {e}",
                code=EXIT_VALIDATION_ERROR,
                json_output=json_output,
            )

    print_info(
        f"Starting Hyperparameter Optimization (paradigm={base_config.retrieval.backend}, "
        f"trials={effective_n_trials}, retrieval_only={base_config.retrieval_only}, "
        f"tune_chunking={effective_tune_chunking}, storage={effective_storage_uri})..."
    )

    # 3. Instantiate and run HyperparameterOpt
    try:
        opt = HyperparameterOpt(
            base_config=base_config,
            hpo_config=hpo_config,
            study_name=hpo_config.study_name,
            storage_uri=effective_storage_uri,
            tune_chunking=effective_tune_chunking,
        )
        result = opt.optimize(
            n_trials=effective_n_trials,
            dataset_path=effective_dataset,
            split=effective_split,
            limit=effective_limit,
        )
    except Exception as e:
        emit_error(
            f"Optimization runtime error: {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    # 4. Optional export of best config
    exported_path_str: str | None = None
    if effective_output is not None and result.best_config is not None:
        try:
            saved_path = result.export_best_config(effective_output)
            exported_path_str = str(saved_path)
            print_info(f"Exported best configuration to: {saved_path}")
        except Exception as e:
            print_info(f"Warning: Failed to export best config to {effective_output}: {e}")

    # 5. Output results
    payload = result.to_dict()
    if exported_path_str:
        payload["exported_config_path"] = exported_path_str

    if json_output:
        print_json(payload)
    else:
        print_success(
            f"Hyperparameter optimization completed ({payload['completed_trials']}/{payload['total_trials']} trials)."
        )
        if result.best_trial:
            print_info(f"Best trial: #{result.best_trial.number}")
            print_info(f"Best parameters: {result.best_trial.params}")
            print_info(
                f"Best values ({result.metric_names}): {result.best_trial.values or result.best_trial.value}"
            )
        if result.pareto_trials:
            print_info(f"Pareto optimal front size: {len(result.pareto_trials)}")

    raise typer.Exit(code=EXIT_SUCCESS)

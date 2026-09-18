"""deepfix eval subcommand."""

import asyncio
from pathlib import Path

import typer

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_json,
    print_success,
)
from deepfix_rag.cli.utils import load_rag_config
from deepfix_rag.evaluation.runner import run_evaluation


def compute_error_reduction(
    baseline_err: int | float | None,
    candidate_err: int | float | None,
    threshold_pct: float = 15.0,
) -> tuple[float | None, bool]:
    """Compute percentage reduction in errors between baseline and candidate.

    Returns:
        tuple[float | None, bool]: (reduction_pct, target_met)
    """
    if baseline_err is None or candidate_err is None:
        return None, False
    if baseline_err > 0:
        reduction_pct = ((baseline_err - candidate_err) / baseline_err) * 100.0
    else:
        reduction_pct = 0.0 if candidate_err == 0 else -100.0
    target_met = bool(reduction_pct >= threshold_pct)
    return reduction_pct, target_met


def eval_cmd(
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path or preset identifier for the evaluation dataset (overrides config.data.path_or_repo).",
    ),
    split: str | None = typer.Option(
        None,
        "--split",
        "-s",
        help="Dataset split to evaluate on (overrides config.data.split).",
    ),
    index_dir: str | None = typer.Option(
        None,
        "--index-dir",
        "-i",
        help="Directory containing the built vector/lexical index (overrides config.retrieval.lancedb_index_dir).",
    ),
    compare: Path | None = typer.Option(
        None,
        "--compare",
        "--baseline",
        help="Optional baseline configuration YAML for delta comparison.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-n",
        help="Limit number of queries to evaluate (overrides config.data.limit).",
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        "-j",
        help="Max parallel QA cases (overrides config.eval.concurrency).",
    ),
    retrieval_only: bool = typer.Option(
        False,
        "--retrieval-only",
        help="Evaluate retrieval only (NDCG, Recall) and skip LLM synthesis and DeepEval judge.",
    ),
) -> None:
    """Run DeepEval evaluation on a dataset using a given RAG configuration."""
    try:
        rag_config = load_rag_config(config)
    except FileNotFoundError:
        emit_error(
            f"Config file not found: {config}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )
    except Exception as e:
        emit_error(
            f"Failed to parse config '{config}': {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    target_dataset = dataset or (rag_config.data.path_or_repo if rag_config.data else None)
    if not target_dataset:
        emit_error(
            "Dataset must be specified either via --dataset flag or within config.data.path_or_repo.",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    target_index_dir = (
        index_dir
        if index_dir is not None
        else (rag_config.retrieval.lancedb_index_dir if rag_config.retrieval else None)
    )
    target_split = split or (rag_config.data.split if rag_config.data else "dev")
    target_limit = (
        limit if limit is not None else (rag_config.data.limit if rag_config.data else None)
    )
    target_concurrency = (
        concurrency
        if concurrency is not None
        else (rag_config.eval.concurrency if rag_config.eval else None)
    )

    target_retrieval_only = rag_config.retrieval_only or retrieval_only

    try:
        candidate_summary = asyncio.run(
            run_evaluation(
                dataset_path=target_dataset,
                config=rag_config,
                index_dir=target_index_dir,
                split=target_split,
                limit=target_limit,
                max_concurrency=target_concurrency,
                retrieval_only=target_retrieval_only,
            )
        )
    except typer.Exit:
        raise
    except RuntimeError as e:
        emit_error(str(e), code=EXIT_RUNTIME_ERROR, json_output=json_output)
    except Exception as e:
        emit_error(
            f"Evaluation failed: {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    candidate_result = {k: v for k, v in candidate_summary.items() if k != "rows"}

    if compare is None:
        if json_output:
            print_json(candidate_result)
        else:
            print_success("Evaluation complete.")
            print_json(candidate_result)
        return

    try:
        baseline_config = load_rag_config(compare)
    except FileNotFoundError:
        emit_error(
            f"Baseline config file not found: {compare}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )
    except Exception as e:
        emit_error(
            f"Failed to parse baseline config '{compare}': {e}",
            code=EXIT_VALIDATION_ERROR,
            json_output=json_output,
        )

    try:
        baseline_retrieval_only = retrieval_only or baseline_config.retrieval_only
        baseline_summary = asyncio.run(
            run_evaluation(
                dataset_path=target_dataset,
                config=baseline_config,
                index_dir=target_index_dir,
                split=target_split,
                limit=target_limit,
                max_concurrency=target_concurrency,
                retrieval_only=baseline_retrieval_only,
            )
        )
    except typer.Exit:
        raise
    except RuntimeError as e:
        emit_error(str(e), code=EXIT_RUNTIME_ERROR, json_output=json_output)
    except Exception as e:
        emit_error(
            f"Baseline evaluation failed: {e}",
            code=EXIT_RUNTIME_ERROR,
            json_output=json_output,
        )

    baseline_result = {k: v for k, v in baseline_summary.items() if k != "rows"}

    delta_keys = [
        "faithfulness_avg",
        "context_precision_avg",
        "context_recall_avg",
        "answer_relevancy_avg",
        "ndcg_avg",
        "recall_avg",
        "precision_avg",
        "deflection_rate",
        "retrieval_induced_error_count",
        "retrieval_induced_error_rate",
        "errors",
    ]
    delta = {}
    for key in delta_keys:
        c_val = candidate_result.get(key)
        b_val = baseline_result.get(key)
        if c_val is None or b_val is None:
            delta[key] = None
        else:
            delta[key] = c_val - b_val

    reduction_pct, target_met = compute_error_reduction(
        baseline_result.get("retrieval_induced_error_count"),
        candidate_result.get("retrieval_induced_error_count"),
    )

    delta["retrieval_induced_error_reduction_pct"] = reduction_pct
    delta["retrieval_induced_error_target_met"] = target_met

    output = {
        "candidate": candidate_result,
        "baseline": baseline_result,
        "delta": delta,
        "retrieval_induced_error_reduction_pct": reduction_pct,
        "retrieval_induced_error_target_met": target_met,
    }

    if json_output:
        print_json(output)
    else:
        print_success("Comparison complete.")
        if reduction_pct is not None:
            status = "MET (>= 15%)" if target_met else "NOT MET (< 15%)"
            typer.secho(
                f"Retrieval-induced error reduction: {reduction_pct:.2f}% [{status}]",
                fg=typer.colors.GREEN if target_met else typer.colors.YELLOW,
                bold=True,
            )
        print_json(output)

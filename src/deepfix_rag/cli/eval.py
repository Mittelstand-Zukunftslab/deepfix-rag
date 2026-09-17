"""deepfix eval subcommand."""

import asyncio
from pathlib import Path

import typer
import yaml

from deepfix_rag.cli.ui import (
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
    emit_error,
    print_json,
    print_success,
)
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.evaluation.runner import run_evaluation


def _load_rag_config(config_path: Path) -> RAGConfig:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RAGConfig.model_validate(data)


def eval_cmd(
    dataset: Path = typer.Option(
        ..., "--dataset", "-d", help="Path or preset identifier for the evaluation dataset."
    ),
    config: Path = typer.Option(
        ..., "--config", "-c", help="Path to RAG configuration YAML file."
    ),
    index_dir: str | None = typer.Option(
        None, "--index-dir", "-i", help="Directory containing the built vector/lexical index."
    ),
    compare: Path | None = typer.Option(
        None, "--compare", help="Optional baseline configuration YAML for delta comparison."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Limit number of queries to evaluate."
    ),
    concurrency: int | None = typer.Option(
        None,
        "--concurrency",
        "-j",
        help="Max parallel QA cases (overrides config). Defaults to config value.",
    ),
) -> None:
    """Run DeepEval evaluation on a dataset using a given RAG configuration."""
    try:
        rag_config = _load_rag_config(config)
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

    try:
        candidate_summary = asyncio.run(
            run_evaluation(
                dataset_path=dataset,
                config=rag_config,
                index_dir=index_dir,
                limit=limit,
                max_concurrency=concurrency,
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
        baseline_config = _load_rag_config(compare)
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
        baseline_summary = asyncio.run(
            run_evaluation(
                dataset_path=dataset,
                config=baseline_config,
                index_dir=index_dir,
                limit=limit,
                max_concurrency=concurrency,
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
        "contextual_precision_avg",
        "answer_correctness_avg",
        "deflection_rate",
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

    output = {
        "candidate": candidate_result,
        "baseline": baseline_result,
        "delta": delta,
    }

    if json_output:
        print_json(output)
    else:
        print_success("Comparison complete.")
        print_json(output)

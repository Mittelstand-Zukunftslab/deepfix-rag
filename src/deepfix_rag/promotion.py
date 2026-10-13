"""Decide whether a candidate config replaces the champion.

The agent names a file. This module validates it, scores it, and promotes it.
Holdout questions are recomputed from the database and are not written anywhere.
"""

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Any

from pydantic import ValidationError
import yaml

from deepfix_rag.cli.eval import metric_delta
from deepfix_rag.config.mutator import compute_config_diff
from deepfix_rag.config.schema import RAGConfig, RetrievalBackend
from deepfix_rag.data import QARecord, qa_record_from_row
from deepfix_rag.evaluation.runner import EvaluationSummary, run_evaluation
from deepfix_rag.feedback import build_questions
from deepfix_rag.indexing import (
    ChampionConfigError,
    IndexCoordinator,
    IndexEmpty,
    UnsupportedIndexBackend,
    bind_locations,
    index_time_signature,
)

_ALLOWED_BACKENDS = frozenset(
    {RetrievalBackend.DENSE, RetrievalBackend.HYBRID, RetrievalBackend.BM25}
)
_HIGHER_IS_BETTER = frozenset(
    {
        "faithfulness",
        "context_precision",
        "context_recall",
        "answer_relevancy",
        "contextual_relevancy",
        "ndcg",
        "recall",
        "precision",
    }
)
_UNREADABLE = "The candidate config could not be read."
_BACKEND = "The candidate uses a retrieval backend that is not allowed."


@dataclass(frozen=True)
class PromotionDecision:
    """The status and numbers to store after a candidate is judged."""

    status: str
    reason: str
    primary_metric: str | None = None
    candidate_config_path: str | None = None
    mlflow_run_id: str | None = None
    summary: str | None = None
    dev_delta: dict[str, float] | None = None
    holdout_delta: dict[str, float] | None = None
    holdout_wins: int | None = None
    holdout_losses: int | None = None


def question_wins(
    candidate_rows: list[dict[str, Any]],
    champion_rows: list[dict[str, Any]],
    metric: str,
) -> tuple[int, int]:
    """Count holdout questions the candidate wins and loses on one metric."""
    champion = {row.get("query_id"): row.get(metric) for row in champion_rows}
    wins = 0
    losses = 0
    for row in candidate_rows:
        left = row.get(metric)
        right = champion.get(row.get("query_id"))
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        if float(left) > float(right):
            wins += 1
        elif float(left) < float(right):
            losses += 1
    return wins, losses


def champion_diff(workspace: Path) -> dict[str, dict[str, Any]]:
    """Diff the current champion against the newest archived champion."""
    current_path = workspace / "configs" / "champion.yaml"
    previous = _previous_champion(workspace)
    if previous is None or not current_path.is_file():
        return {}
    try:
        current = RAGConfig.from_yaml(current_path)
        archived = RAGConfig.from_yaml(previous)
    except (OSError, ValidationError, ValueError, yaml.YAMLError):
        return {}
    return {
        key: {"before": before, "after": after}
        for key, (before, after) in compute_config_diff(archived, current).items()
    }


def apply_candidate(
    db_path: Path,
    workspace: Path,
    *,
    snapshot_id: str,
    candidate_config_path: str,
    primary_metric: str,
    summary: str,
    mlflow_run_id: str | None,
    min_primary_delta: float,
    coordinator: IndexCoordinator | None,
    private_paths: tuple[str, ...] = (),
) -> PromotionDecision:
    """Validate, score, and maybe promote. Agent-reported numbers are only stored."""
    metric = primary_metric.strip().lower()
    base = PromotionDecision(
        status="rejected",
        reason=_UNREADABLE,
        primary_metric=metric,
        candidate_config_path=candidate_config_path,
        mlflow_run_id=mlflow_run_id,
        summary=summary,
    )
    loaded = _load_candidate(workspace, candidate_config_path, private_paths)
    if isinstance(loaded, str):
        return PromotionDecision(
            status="rejected",
            reason=loaded,
            primary_metric=metric,
            candidate_config_path=candidate_config_path,
            mlflow_run_id=mlflow_run_id,
            summary=summary,
        )
    try:
        champion = RAGConfig.from_yaml(workspace / "configs" / "champion.yaml")
    except (OSError, ValidationError, ValueError, yaml.YAMLError):
        return _failed(base, "The champion config could not be read.")

    index_changed = index_time_signature(loaded) != index_time_signature(champion)
    serving = workspace / "indexes" / "current"
    candidate_index = serving
    build_id: str | None = None
    if index_changed:
        if coordinator is None:
            return _failed(base, "The candidate index could not be built.")
        try:
            build_id = coordinator.build_beside(loaded)
        except (
            IndexEmpty,
            UnsupportedIndexBackend,
            ChampionConfigError,
            OSError,
            ValueError,
        ) as exc:
            return _failed(base, str(exc).strip() or "The candidate index could not be built.")
        except Exception as exc:
            return _failed(base, str(exc).strip() or "The candidate index could not be built.")
        candidate_index = workspace / "indexes" / "builds" / build_id

    try:
        dev_records = _load_dev(workspace / "feedback" / snapshot_id / "feedback_dev.jsonl")
        holdout_records = _holdout_records(db_path)
        embed_model = None if coordinator is None else coordinator.embed_model
        llm = None if coordinator is None else coordinator.llm
        scored = asyncio.run(
            _score_pair(
                champion=champion,
                candidate=loaded,
                workspace=workspace,
                champion_index=serving,
                candidate_index=candidate_index,
                dev_records=dev_records,
                holdout_records=holdout_records,
                embed_model=embed_model,
                llm=llm,
            )
        )
    except Exception as exc:
        if coordinator is not None and build_id is not None:
            coordinator.discard_build(build_id)
        message = str(exc).strip() or "The candidate could not be scored."
        return _failed(base, message)

    dev_delta = _plain_delta(scored["dev_delta"])
    holdout_delta = _plain_delta(scored["holdout_delta"])
    wins, losses = question_wins(scored["holdout_candidate"], scored["holdout_champion"], metric)
    rejection = _rejection(dev_delta, holdout_delta, metric, min_primary_delta, wins, losses)
    judged = PromotionDecision(
        status="rejected" if rejection else "promoted",
        reason=rejection or summary,
        primary_metric=metric,
        candidate_config_path=candidate_config_path,
        mlflow_run_id=mlflow_run_id,
        summary=summary,
        dev_delta=dev_delta,
        holdout_delta=holdout_delta,
        holdout_wins=wins,
        holdout_losses=losses,
    )
    if rejection:
        if coordinator is not None and build_id is not None:
            coordinator.discard_build(build_id)
        return judged
    bound = bind_locations(loaded, workspace, serving)
    bound.retrieval.load_if_exists = True
    try:
        _commit(workspace, bound, coordinator, build_id)
    except Exception as exc:
        if coordinator is not None and build_id is not None:
            coordinator.discard_build(build_id)
        return _failed(judged, str(exc).strip() or "The champion could not be replaced.")
    return judged


def _failed(base: PromotionDecision, reason: str) -> PromotionDecision:
    return PromotionDecision(
        status="failed",
        reason=reason,
        primary_metric=base.primary_metric,
        candidate_config_path=base.candidate_config_path,
        mlflow_run_id=base.mlflow_run_id,
        summary=base.summary,
        dev_delta=base.dev_delta,
        holdout_delta=base.holdout_delta,
        holdout_wins=base.holdout_wins,
        holdout_losses=base.holdout_losses,
    )


def _load_candidate(
    workspace: Path,
    raw_path: str,
    private_paths: tuple[str, ...],
) -> RAGConfig | str:
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace / path
    try:
        resolved = path.resolve()
    except OSError:
        return _UNREADABLE
    for private in private_paths:
        try:
            resolved.relative_to(Path(private).resolve())
        except ValueError:
            continue
        return _UNREADABLE
    if not resolved.is_file():
        return _UNREADABLE
    try:
        config = RAGConfig.from_yaml(resolved)
    except (OSError, ValidationError, ValueError, yaml.YAMLError):
        return _UNREADABLE
    if config.retrieval.backend not in _ALLOWED_BACKENDS:
        return _BACKEND
    return config


def _load_dev(path: Path) -> list[QARecord]:
    if not path.is_file():
        raise FileNotFoundError("The dev snapshot could not be read.")
    records: list[QARecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(qa_record_from_row(json.loads(line)))
    return records


def _holdout_records(db_path: Path) -> list[QARecord]:
    return [
        qa_record_from_row(question.row())
        for question in build_questions(db_path)
        if question.holdout
    ]


async def _score_pair(
    *,
    champion: RAGConfig,
    candidate: RAGConfig,
    workspace: Path,
    champion_index: Path,
    candidate_index: Path,
    dev_records: list[QARecord],
    holdout_records: list[QARecord],
    embed_model: Any,
    llm: Any,
) -> dict[str, Any]:
    champion_config = _scoring_config(champion, workspace, champion_index)
    candidate_config = _scoring_config(candidate, workspace, candidate_index)
    dev_champion = await _score(champion_config, dev_records, embed_model, llm)
    dev_candidate = await _score(candidate_config, dev_records, embed_model, llm)
    holdout_champion = await _score(champion_config, holdout_records, embed_model, llm)
    holdout_candidate = await _score(candidate_config, holdout_records, embed_model, llm)
    return {
        "dev_delta": metric_delta(dev_candidate.to_dict(), dev_champion.to_dict()),
        "holdout_delta": metric_delta(holdout_candidate.to_dict(), holdout_champion.to_dict()),
        "holdout_candidate": holdout_candidate.rows,
        "holdout_champion": holdout_champion.rows,
    }


def _scoring_config(config: RAGConfig, workspace: Path, index_dir: Path) -> RAGConfig:
    bound = bind_locations(config, workspace, index_dir)
    retrieval = bound.retrieval.model_copy(update={"load_if_exists": True})
    return bound.model_copy(update={"retrieval": retrieval, "retrieval_only": True})


async def _score(
    config: RAGConfig,
    records: list[QARecord],
    embed_model: Any,
    llm: Any,
) -> EvaluationSummary:
    return await run_evaluation(
        config=config,
        records=records,
        retrieval_only=True,
        embed_model=embed_model,
        llm=llm,
        persist=False,
        run_name="optimization-gate",
    )


def _plain_delta(delta: dict[str, Any]) -> dict[str, float]:
    plain: dict[str, float] = {}
    for key, value in delta.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        plain[key] = float(value)
    return plain


def _rejection(
    dev_delta: dict[str, float],
    holdout_delta: dict[str, float],
    metric: str,
    minimum: float,
    wins: int,
    losses: int,
) -> str | None:
    if metric not in dev_delta:
        return f"The dev set did not score {metric}."
    if dev_delta[metric] < minimum:
        return f"The dev {metric} delta is {dev_delta[metric]:.3f}, and {minimum:.3f} is required."
    for key, value in dev_delta.items():
        if key == metric or key not in _HIGHER_IS_BETTER:
            continue
        if value < 0:
            return f"The dev metric {key} fell by {abs(value):.3f}."
    if metric not in holdout_delta:
        return f"The holdout set did not score {metric}."
    if holdout_delta[metric] < 0:
        return f"The holdout {metric} delta is {holdout_delta[metric]:.3f}."
    if wins < losses:
        return "The candidate lost more holdout questions than it won."
    return None


def _commit(
    workspace: Path,
    config: RAGConfig,
    coordinator: IndexCoordinator | None,
    build_id: str | None,
) -> None:
    champion = workspace / "configs" / "champion.yaml"
    previous = champion.read_text(encoding="utf-8") if champion.is_file() else None
    previous_pointer = None if coordinator is None else coordinator.serving_build_id()
    archive = _archive_champion(champion)
    try:
        _replace_text(champion, config.to_yaml())
        if coordinator is None:
            return
        if build_id is None:
            coordinator.reload_champion()
        else:
            coordinator.switch_serving(build_id)
    except Exception:
        if previous is not None:
            _replace_text(champion, previous)
        if archive is not None:
            archive.unlink(missing_ok=True)
        if coordinator is not None and previous_pointer is not None:
            if coordinator.serving_build_id() != previous_pointer:
                coordinator.switch_serving(previous_pointer)
            else:
                coordinator.reload_champion()
        raise


def _archive_champion(champion: Path) -> Path | None:
    if not champion.is_file():
        return None
    history = champion.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = _history_name(history)
    archive = history / stamp
    shutil.copy2(champion, archive)
    return archive


def _history_name(history: Path) -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{stamp}.yaml"
    if (history / name).exists():
        name = f"{stamp}-2.yaml"
    return name


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _previous_champion(workspace: Path) -> Path | None:
    history = workspace / "configs" / "history"
    if not history.is_dir():
        return None
    files = sorted(path for path in history.glob("*.yaml") if path.is_file())
    if not files:
        return None
    return files[-1]

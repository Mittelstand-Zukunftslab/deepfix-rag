"""Paired per-query gate used by `deepfix gate` and described in the optimization skill."""

from collections.abc import Mapping
import random
from typing import Any

from deepfix_rag.config.settings import settings
from deepfix_rag.promotion import _HIGHER_IS_BETTER

BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 0


class GateInputError(ValueError):
    """The compare file cannot be judged."""


def _questions(side: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Map query id to numeric metrics from `questions` or flat `rows`."""
    found: dict[str, dict[str, float]] = {}
    raw_questions = side.get("questions")
    if isinstance(raw_questions, list) and raw_questions:
        for item in raw_questions:
            if not isinstance(item, dict) or item.get("query_id") is None:
                continue
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            found[str(item["query_id"])] = {
                str(key): float(value)
                for key, value in metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        return found
    rows = side.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("query_id") is None:
                continue
            found[str(row["query_id"])] = {
                str(key): float(value)
                for key, value in row.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
    return found


def paired_deltas(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    metric: str,
) -> list[float]:
    """Candidate minus baseline for queries scored on both sides."""
    left = _questions(candidate)
    right = _questions(baseline)
    deltas: list[float] = []
    for query_id, metrics in left.items():
        if query_id not in right or metric not in metrics or metric not in right[query_id]:
            continue
        deltas.append(metrics[metric] - right[query_id][metric])
    return deltas


def bootstrap_ci(
    deltas: list[float],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Percentile interval of the mean, from a seeded bootstrap."""
    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(seed)
    count = len(deltas)
    means = []
    for _ in range(samples):
        draw = [deltas[rng.randrange(count)] for _ in range(count)]
        means.append(sum(draw) / count)
    means.sort()
    low_at = int(0.025 * (samples - 1))
    high_at = int(0.975 * (samples - 1))
    return (means[low_at], means[high_at])


def _summary_delta(
    compare: Mapping[str, Any], metric: str, paired_mean: float | None
) -> float | None:
    if paired_mean is not None:
        return paired_mean
    delta = compare.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get(metric), (int, float)):
        return float(delta[metric])
    return None


def _backend_reason(side: Mapping[str, Any], label: str) -> str | None:
    configured = side.get("configured_backend")
    effective = side.get("effective_backend")
    if not configured or not effective or configured == effective:
        return None
    return (
        f"The {label} effective backend is {effective}, "
        f"not the configured {configured}. The scores are not that backend."
    )


def judge_compare(
    compare: Mapping[str, Any],
    primary: str,
    *,
    min_delta: float | None = None,
    secondary_tolerance: float | None = None,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Decide keep or revert from one `eval --compare` payload.

    Pass when the paired primary mean is at least `min_delta`, the seeded
    bootstrap interval lies entirely above 0, no higher-is-better metric falls
    past the secondary tolerance, both sides scored every case, and neither
    side silently changed backend.
    """
    if primary not in _HIGHER_IS_BETTER:
        raise GateInputError(f"Primary metric '{primary}' is not a higher-is-better gate metric.")
    candidate = compare.get("candidate")
    baseline = compare.get("baseline")
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        raise GateInputError("Compare JSON must contain candidate and baseline objects.")

    threshold = settings.OPTIMIZATION_MIN_PRIMARY_DELTA if min_delta is None else min_delta
    tolerance = (
        settings.OPTIMIZATION_SECONDARY_TOLERANCE
        if secondary_tolerance is None
        else secondary_tolerance
    )
    deltas = paired_deltas(candidate, baseline, primary)
    if not deltas:
        raise GateInputError(
            f"Compare JSON has no paired per-query values for '{primary}'. "
            "Re-run eval --compare so the JSON includes questions."
        )

    paired_mean = sum(deltas) / len(deltas)
    low, high = bootstrap_ci(deltas, seed=seed)
    wins = sum(1 for value in deltas if value > 0)
    losses = sum(1 for value in deltas if value < 0)
    ties = len(deltas) - wins - losses

    delta_block = compare.get("delta") if isinstance(compare.get("delta"), dict) else {}
    regressed: list[str] = []
    for key in sorted(_HIGHER_IS_BETTER):
        if key == primary or key not in delta_block:
            continue
        value = delta_block[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < tolerance:
            regressed.append(key)

    reasons: list[str] = []
    if paired_mean < threshold:
        reasons.append(
            f"The {primary} delta is {paired_mean:.4f}, and {threshold:.4f} is required."
        )
    if low <= 0:
        reasons.append(f"The {primary} 95% interval [{low:.4f}, {high:.4f}] includes 0 or below.")
    if regressed:
        reasons.append(
            f"Regressed past the secondary tolerance ({tolerance:.4f}): {', '.join(regressed)}."
        )
    for label, side in (("candidate", candidate), ("baseline", baseline)):
        errors = side.get("errors") or 0
        if isinstance(errors, int) and errors > 0:
            reasons.append(f"The {label} has {errors} errored cases.")
        backend_reason = _backend_reason(side, label)
        if backend_reason:
            reasons.append(backend_reason)
        judge_failures = side.get("judge_failures") or 0
        if isinstance(judge_failures, int) and judge_failures > 0:
            reasons.append(f"The {label} has {judge_failures} judge metric failures.")

    passed = not reasons
    return {
        "pass": passed,
        "decision": "keep" if passed else "revert",
        "primary": primary,
        "primary_delta": paired_mean,
        "min_delta": threshold,
        "secondary_tolerance": tolerance,
        "ci95": [low, high],
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "regressed": regressed,
        "reasons": reasons,
        "summary_delta": _summary_delta(compare, primary, None),
    }

#!/usr/bin/env python3
"""
Live RBAC smoke test for deepfix_mlflow.MLflowEvidenceReader.

Safe by default:
- verifies allowed reads in dev-workspace / target-rag
- verifies the same reader identity cannot discover the Hermes experiment
- verifies the same reader identity cannot discover the hidden-eval experiment
  in test-workspace
- verifies omitting X-MLFLOW-WORKSPACE does not reveal target-rag

Optional controlled mutation probe:
    --write-probes

The mutation probe tries to create a uniquely tagged run in target-rag. The
expected result for a reader identity is HTTP 403/401. If creation unexpectedly
succeeds, the script marks the RBAC test as FAILED and immediately attempts to
delete the temporary run. This can leave a temporary run behind if cleanup is
also denied or otherwise fails.

Run from a checkout where `deepfix_mlflow` is importable, for example:

    python test_mlflow_client_rbac.py --principal hermes-reader
    python test_mlflow_client_rbac.py --principal deepfix-reader
    python test_mlflow_client_rbac.py --principal all
    python test_mlflow_client_rbac.py --principal hermes-reader --write-probes

Required environment variables are the same as `.env.example`.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable
import uuid

from deepfix_mlflow import MLflowEvidenceReader, MLflowReadConfig, MLflowReadError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]

load_dotenv(ROOT / ".env")

DENIAL_CODES = {400, 401, 403, 404}


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pass_(self, label: str, detail: str | None = None) -> None:
        self.passed += 1
        suffix = f" — {detail}" if detail else ""
        print(f"[PASS] {label}{suffix}")

    def fail(self, label: str, detail: str | None = None) -> None:
        self.failed += 1
        suffix = f" — {detail}" if detail else ""
        print(f"[FAIL] {label}{suffix}")

    def skip(self, label: str, detail: str | None = None) -> None:
        self.skipped += 1
        suffix = f" — {detail}" if detail else ""
        print(f"[SKIP] {label}{suffix}")


def _http_status(exc: BaseException) -> int | None:
    match = re.search(r"HTTP\s+(\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _expect_denied(
    results: Results,
    label: str,
    action: Callable[[], Any],
    *,
    accepted_statuses: set[int] = DENIAL_CODES,
) -> None:
    try:
        value = action()
    except MLflowReadError as exc:
        status = _http_status(exc)
        if status in accepted_statuses:
            results.pass_(label, f"denied with HTTP {status}")
        else:
            results.fail(label, f"unexpected error: {exc}")
        return
    except Exception as exc:
        results.fail(label, f"unexpected exception: {type(exc).__name__}: {exc}")
        return

    results.fail(label, f"unexpectedly succeeded: {value!r}")


def _expect_read(
    results: Results,
    label: str,
    action: Callable[[], Any],
) -> Any | None:
    try:
        value = action()
    except Exception as exc:
        results.fail(label, f"{type(exc).__name__}: {exc}")
        return None
    results.pass_(label)
    return value


def _reader_config(principal: str) -> MLflowReadConfig:
    if principal == "hermes-reader":
        return MLflowReadConfig.for_hermes_from_env()
    if principal == "deepfix-reader":
        return MLflowReadConfig.for_deepfix_from_env()
    raise ValueError(f"Unsupported principal: {principal}")


def _first_run_id(payload: dict[str, Any]) -> str | None:
    runs = payload.get("runs") or []
    if not runs:
        return None
    run = runs[0]
    info = run.get("info") or {}
    return info.get("run_id") or info.get("run_uuid") or run.get("run_id")


def _first_trace_id(payload: dict[str, Any]) -> str | None:
    traces = payload.get("traces") or []
    if not traces:
        return None
    trace = traces[0]
    info = trace.get("trace_info") or trace.get("info") or trace
    return info.get("trace_id") or trace.get("trace_id")


def _first_model_id(payload: dict[str, Any]) -> str | None:
    models = payload.get("models") or []
    if not models:
        return None
    model = models[0]
    return model.get("model_id") or model.get("id")


def _probe_allowed_reads(results: Results, reader: MLflowEvidenceReader) -> None:
    experiment = _expect_read(
        results,
        "resolve target-rag experiment",
        reader.get_target_experiment,
    )
    if experiment is None:
        return

    results.pass_(
        "target-rag experiment id resolved",
        str(experiment.get("experiment_id", reader.experiment_id)),
    )

    runs = _expect_read(
        results,
        "search target-rag runs",
        lambda: reader.search_runs(max_results=1),
    )
    if isinstance(runs, dict):
        run_id = _first_run_id(runs)
        if run_id:
            _expect_read(
                results,
                f"read run {run_id}",
                lambda run_id=run_id: reader.get_run(run_id),
            )
        else:
            results.skip("read one concrete run", "no runs returned")

    traces = _expect_read(
        results,
        "search target-rag traces",
        lambda: reader.search_traces(max_results=1),
    )
    if isinstance(traces, dict):
        trace_id = _first_trace_id(traces)
        if trace_id:
            _expect_read(
                results,
                f"read trace metadata {trace_id}",
                lambda trace_id=trace_id: reader.get_trace_info(trace_id),
            )
            _expect_read(
                results,
                f"read full trace {trace_id}",
                lambda trace_id=trace_id: reader.get_trace(trace_id),
            )
        else:
            results.skip("read one concrete trace", "no traces returned")

    models = _expect_read(
        results,
        "search target-rag LoggedModels",
        lambda: reader.search_logged_models(max_results=1),
    )
    if isinstance(models, dict):
        model_id = _first_model_id(models)
        if model_id:
            _expect_read(
                results,
                f"read LoggedModel {model_id}",
                lambda model_id=model_id: reader.get_logged_model(model_id),
            )
            _expect_read(
                results,
                f"list LoggedModel artifacts {model_id}",
                lambda model_id=model_id: reader.list_logged_model_artifacts(model_id),
            )
        else:
            results.skip("read one concrete LoggedModel", "no models returned")


def _probe_denied_resources(
    results: Results,
    config: MLflowReadConfig,
) -> None:
    import os

    hermes_experiment = os.environ.get("MLFLOW_DEV_HERMES_EXPERIMENT")
    if hermes_experiment:
        hermes_reader = MLflowEvidenceReader(
            replace(config, experiment_name=hermes_experiment)
        )
        _expect_denied(
            results,
            f"{config.username} cannot discover dev Hermes experiment",
            hermes_reader.get_target_experiment,
        )
    else:
        results.skip(
            "deny dev Hermes experiment",
            "MLFLOW_DEV_HERMES_EXPERIMENT not set",
        )

    test_workspace = os.environ.get("MLFLOW_TEST_WORKSPACE")
    hidden_experiment = os.environ.get("MLFLOW_HIDDEN_EVAL_EXPERIMENT")
    if test_workspace and hidden_experiment:
        hidden_reader = MLflowEvidenceReader(
            replace(
                config,
                workspace=test_workspace,
                experiment_name=hidden_experiment,
            )
        )
        _expect_denied(
            results,
            f"{config.username} cannot discover hidden-eval experiment",
            hidden_reader.get_target_experiment,
        )
    else:
        missing = [
            name
            for name, value in (
                ("MLFLOW_TEST_WORKSPACE", test_workspace),
                ("MLFLOW_HIDDEN_EVAL_EXPERIMENT", hidden_experiment),
            )
            if not value
        ]
        results.skip(
            "deny hidden-eval experiment",
            "missing " + ", ".join(missing),
        )

    # Explicitly test that omission of the workspace header cannot bypass isolation.
    no_workspace_reader = MLflowEvidenceReader(config)
    no_workspace_reader._headers.pop("X-MLFLOW-WORKSPACE", None)  # test-only
    _expect_denied(
        results,
        f"{config.username} cannot access target-rag via default-workspace fallback",
        no_workspace_reader.get_target_experiment,
    )


def _probe_write_denial(
    results: Results,
    reader: MLflowEvidenceReader,
) -> None:
    """
    Controlled mutation probe.

    Expected: create-run is denied.
    Unexpected success: flag failure, then immediately try to delete the run.
    """
    probe_name = f"rbac-probe-{uuid.uuid4().hex[:12]}"
    now_ms = int(time.time() * 1000)
    body = {
        "experiment_id": reader.experiment_id,
        "start_time": now_ms,
        "tags": [
            {"key": "mlflow.runName", "value": probe_name},
            {"key": "rbac_probe", "value": "true"},
            {"key": "rbac_probe_principal", "value": reader.config.username},
        ],
    }

    try:
        created = reader._request(  # test-only use of the low-level transport
            "POST",
            "/api/2.0/mlflow/runs/create",
            body=body,
        )
    except MLflowReadError as exc:
        status = _http_status(exc)
        if status in {401, 403}:
            results.pass_(
                f"{reader.config.username} cannot create a run in target-rag",
                f"denied with HTTP {status}",
            )
        else:
            results.fail(
                f"{reader.config.username} write probe",
                f"unexpected response: {exc}",
            )
        return
    except Exception as exc:
        results.fail(
            f"{reader.config.username} write probe",
            f"{type(exc).__name__}: {exc}",
        )
        return

    run = created.get("run") or {}
    info = run.get("info") or {}
    run_id = info.get("run_id") or info.get("run_uuid") or run.get("run_id")

    results.fail(
        f"{reader.config.username} cannot create a run in target-rag",
        f"RBAC VIOLATION: create succeeded (run_id={run_id!r})",
    )

    if not run_id:
        results.fail(
            "cleanup unexpected write",
            "run was created but response contained no run_id",
        )
        return

    try:
        reader._request(
            "POST",
            "/api/2.0/mlflow/runs/delete",
            body={"run_id": run_id},
        )
    except Exception as exc:
        results.fail(
            "cleanup unexpected write",
            f"could not delete temporary run {run_id}: {exc}",
        )
    else:
        results.pass_(
            "cleanup unexpected write",
            f"deleted temporary run {run_id}",
        )


def run_principal(principal: str, *, write_probes: bool) -> Results:
    print(f"\n=== RBAC test: {principal} ===")
    results = Results()

    try:
        config = _reader_config(principal)
    except Exception as exc:
        results.fail("load reader configuration", str(exc))
        return results

    print(
        f"tracking_uri={config.tracking_uri}\n"
        f"workspace={config.workspace}\n"
        f"experiment={config.experiment_name}\n"
        f"username={config.username}"
    )

    reader = MLflowEvidenceReader(config)
    _probe_allowed_reads(results, reader)
    _probe_denied_resources(results, config)

    if write_probes:
        _probe_write_denial(results, reader)
    else:
        results.skip(
            "write-denial probe",
            "safe mode; re-run with --write-probes for controlled mutation test",
        )

    print(
        f"\nResult for {principal}: "
        f"{results.passed} passed, {results.failed} failed, {results.skipped} skipped"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live RBAC smoke test for MLflow reader identities."
    )
    parser.add_argument(
        "--principal",
        choices=("hermes-reader", "deepfix-reader", "all"),
        default="hermes-reader",
        help="Reader identity to test (default: hermes-reader).",
    )
    parser.add_argument(
        "--write-probes",
        action="store_true",
        help=(
            "Also test that create-run is denied. If RBAC is too broad, this can "
            "briefly create a temporary run; the script immediately attempts cleanup."
        ),
    )
    args = parser.parse_args()

    principals = (
        ["hermes-reader", "deepfix-reader"]
        if args.principal == "all"
        else [args.principal]
    )

    total_failed = 0
    for principal in principals:
        result = run_principal(principal, write_probes=args.write_probes)
        total_failed += result.failed

    if total_failed:
        print(f"\nRBAC CHECK FAILED: {total_failed} failing assertion(s).")
        return 1

    print("\nRBAC CHECK PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

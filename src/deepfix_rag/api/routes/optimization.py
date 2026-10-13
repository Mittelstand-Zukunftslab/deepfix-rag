"""Admin routes for the optimization schedule and for Run now."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.agent_process import AgentUnavailable
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_REQUEST,
    CONFLICT,
    FORBIDDEN,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.schemas import (
    ConfigChange,
    OptimizationRunResponse,
    OptimizationRunSummary,
    OptimizationStatusResponse,
    StartOptimizationRequest,
    UpdateOptimizationRequest,
)
from deepfix_rag.optimization import (
    OptimizationRun,
    RunInProgress,
    mlflow_run_url,
    recent_runs,
    settings_from,
    start_optimization_run,
)
from deepfix_rag.optimization_schedule import InvalidSchedule
from deepfix_rag.promotion import champion_diff

router = APIRouter(tags=["optimization"])


@router.get(
    "/admin/optimization",
    operation_id="get_optimization_status",
    response_model=OptimizationStatusResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN),
    summary="Show the optimization schedule and recent runs",
)
def get_optimization_status(request: Request) -> OptimizationStatusResponse:
    """Return the nightly schedule, the champion diff, and recent runs."""
    _require_admin(request, "Only admins can view optimization.")
    return _status(request)


@router.patch(
    "/admin/optimization",
    operation_id="update_optimization_schedule",
    response_model=OptimizationStatusResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, BAD_REQUEST),
    summary="Change the nightly optimization schedule",
)
def update_optimization_schedule(
    payload: UpdateOptimizationRequest,
    request: Request,
) -> OptimizationStatusResponse:
    """Store a five-field cron expression. An invalid one leaves the schedule unchanged."""
    _require_admin(request, "Only admins can change the optimization schedule.")
    try:
        request.app.state.optimization.update(payload.cron, payload.enabled)
    except InvalidSchedule as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    return _status(request)


@router.post(
    "/admin/optimization/run",
    operation_id="start_optimization_run",
    response_model=OptimizationRunResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, CONFLICT, SERVICE_UNAVAILABLE),
    summary="Start the optimization job now",
)
def start_optimization_run_route(
    _payload: StartOptimizationRequest,
    request: Request,
) -> OptimizationRunResponse:
    """Export feedback and start one run, or skip it when too little is labeled."""
    _require_admin(request, "Only admins can start an optimization run.")
    settings = get_settings(request)
    try:
        run = start_optimization_run(
            settings.product_db_path,
            settings.workspace_dir,
            trigger="manual",
            options=settings_from(settings),
            coordinator=getattr(request.app.state, "index", None),
        )
    except RunInProgress as exc:
        raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return _response(run)


def _require_admin(request: Request, detail: str) -> None:
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=FORBIDDEN, detail=detail)


def _status(request: Request) -> OptimizationStatusResponse:
    settings = get_settings(request)
    schedule = request.app.state.optimization.view()
    workspace = settings.workspace_dir
    runs = [
        OptimizationRunSummary(
            run_id=run.run_id,
            status=run.status,
            trigger=run.trigger,
            reason=run.reason,
            primary_metric=run.primary_metric,
            dev_delta=run.dev_delta,
            holdout_delta=run.holdout_delta,
            holdout_wins=run.holdout_wins,
            holdout_losses=run.holdout_losses,
            mlflow_run_id=run.mlflow_run_id,
            mlflow_url=mlflow_run_url(run.mlflow_run_id),
            started_at=run.started_at,
            ended_at=run.ended_at,
        )
        for run in recent_runs(settings.product_db_path)
    ]
    return OptimizationStatusResponse(
        cron=schedule.cron,
        timezone=schedule.timezone,
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        agent_available=schedule.agent_available,
        champion_config_path=str(workspace / "configs" / "champion.yaml"),
        champion_diff={
            key: ConfigChange(before=_scalar(change["before"]), after=_scalar(change["after"]))
            for key, change in champion_diff(workspace).items()
        },
        runs=runs,
    )


def _scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _response(run: OptimizationRun) -> OptimizationRunResponse:
    return OptimizationRunResponse(
        run_id=run.run_id,
        status=run.status,
        trigger=run.trigger,
        reason=run.reason,
        dev_count=run.dev_count,
        new_dev_count=run.new_dev_count,
        holdout_count=run.holdout_count,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )

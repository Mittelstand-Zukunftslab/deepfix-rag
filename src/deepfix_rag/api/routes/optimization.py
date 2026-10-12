"""Admin route that starts the same optimization job the scheduler will call."""

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.agent_process import HermesUnavailable
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    CONFLICT,
    FORBIDDEN,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.schemas import OptimizationRunResponse, StartOptimizationRequest
from deepfix_rag.optimization import (
    OptimizationRun,
    RunInProgress,
    settings_from,
    start_optimization_run,
)

router = APIRouter(tags=["optimization"])


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
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(
            status_code=FORBIDDEN, detail="Only admins can start an optimization run."
        )
    settings = get_settings(request)
    try:
        run = start_optimization_run(
            settings.product_db_path,
            settings.workspace_dir,
            trigger="manual",
            options=settings_from(settings),
        )
    except RunInProgress as exc:
        raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc
    except HermesUnavailable as exc:
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return _response(run)


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

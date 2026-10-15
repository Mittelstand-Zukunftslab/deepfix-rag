"""Liveness route."""

from fastapi import APIRouter

from deepfix_rag.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    operation_id="health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
async def health() -> HealthResponse:
    """Report that the process is up. Touches no LLM, embedding, MLflow, or agent backend."""
    return HealthResponse()

"""Index status and admin repair rebuilds."""

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.api.deps import current_user
from deepfix_rag.api.errors import BAD_REQUEST, CONFLICT, FORBIDDEN, UNAUTHORIZED, error_responses
from deepfix_rag.api.schemas import IndexStatusResponse
from deepfix_rag.indexing import (
    ChampionConfigError,
    IndexCoordinator,
    IndexEmpty,
    IndexSnapshot,
    RebuildInProgress,
    UnsupportedIndexBackend,
)

router = APIRouter(tags=["index"])


def _snapshot(snapshot: IndexSnapshot) -> IndexStatusResponse:
    return IndexStatusResponse(
        status=snapshot.status,
        document_count=snapshot.document_count,
        chunk_count=snapshot.chunk_count,
        pending_document_count=snapshot.pending_document_count,
        updated_at=snapshot.updated_at,
        config_path=snapshot.config_path,
        serving_build_id=snapshot.serving_build_id,
        error=snapshot.error,
    )


def _coordinator(request: Request) -> IndexCoordinator:
    return request.app.state.index


@router.get(
    "/index",
    operation_id="get_index_status",
    response_model=IndexStatusResponse,
    responses=error_responses(UNAUTHORIZED),
    summary="Read index status",
)
def get_index_status(request: Request) -> IndexStatusResponse:
    """Return the shared index status for any signed-in user."""
    current_user(request)
    return _snapshot(_coordinator(request).snapshot())


@router.post(
    "/admin/index/rebuild",
    operation_id="rebuild_index",
    response_model=IndexStatusResponse,
    status_code=202,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED, FORBIDDEN, CONFLICT),
    summary="Rebuild the index",
)
def rebuild_index(request: Request) -> IndexStatusResponse:
    """Start a full repair rebuild. The current index keeps serving until it succeeds."""
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=FORBIDDEN, detail="Only admins can rebuild the index.")
    try:
        snapshot = _coordinator(request).request_rebuild()
    except RebuildInProgress as exc:
        raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc
    except UnsupportedIndexBackend as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    except (IndexEmpty, ChampionConfigError) as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    return _snapshot(snapshot)

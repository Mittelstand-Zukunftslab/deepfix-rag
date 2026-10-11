"""Search the shared index without calling the generation model."""

from pathlib import Path
import uuid

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_REQUEST,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.schemas import HitResponse, QueryRequest, RetrievalResponse
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.documents import document_filenames
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.interactions import record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.tracing import trace_request

router = APIRouter(tags=["retrieval"])

INDEX_PREPARING = "The index is being prepared. Try again in a minute."


def displayed_limit(config: RAGConfig, maximum: int) -> int:
    """How many passages to show: retrieval depth, or rerank depth, then the display cap."""
    depth = config.rerank.top_n if config.rerank.enabled else config.retrieval.top_k
    return max(0, min(depth, maximum))


def _coordinator(request: Request) -> IndexCoordinator:
    return request.app.state.index


@router.post(
    "/retrieval",
    operation_id="retrieve",
    response_model=RetrievalResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED, SERVICE_UNAVAILABLE),
    summary="Search the corpus",
)
async def retrieve(payload: QueryRequest, request: Request) -> RetrievalResponse:
    """Return ranked passages. This route never calls the generation model."""
    user = current_user(request)
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=BAD_REQUEST, detail="Enter a query.")
    settings = get_settings(request)
    coordinator = _coordinator(request)
    lease = coordinator.checkout()
    if lease is None:
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=INDEX_PREPARING)
    try:
        found = await lease.service.retrieve(query)
        limit = displayed_limit(lease.service.config, settings.MAX_DISPLAYED_HITS)
        build_id = lease.build_id
        config_path = coordinator.snapshot().config_path
    finally:
        coordinator.release(lease)
    return _store_retrieval(
        settings.product_db_path,
        user_id=user.id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=found[:limit],
    )


def _store_retrieval(
    db_path: Path,
    *,
    user_id: str,
    query: str,
    build_id: str,
    config_path: str,
    results: list[RetrievalResult],
) -> RetrievalResponse:
    interaction_id = uuid.uuid4().hex
    trace_id = trace_request(
        "retrieval",
        interaction_id,
        {"query": query},
        {
            "hits": [
                {
                    "doc_id": result.doc_id,
                    "score": float(result.score or 0.0),
                    "text": result.text,
                }
                for result in results
            ]
        },
    )
    recorded = record_interaction(
        db_path,
        kind="retrieval",
        user_id=user_id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=results,
        trace_id=trace_id,
        interaction_id=interaction_id,
    )
    names = document_filenames(db_path, [hit.doc_id for hit in recorded.hits])
    return RetrievalResponse(
        interaction_id=recorded.interaction_id,
        hits=[
            HitResponse(
                rank=hit.rank,
                doc_id=hit.doc_id,
                document_filename=names.get(hit.doc_id, hit.doc_id),
                text=hit.text,
                score=hit.score,
                vote=None,
            )
            for hit in recorded.hits
        ],
    )

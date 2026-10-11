"""Search the shared index, and answer one question from it."""

from pathlib import Path
from typing import Literal, cast
import uuid

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_REQUEST,
    NOT_FOUND,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.schemas import (
    AskResponse,
    HitResponse,
    InteractionResponse,
    QueryRequest,
    RetrievalResponse,
)
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.documents import document_filenames
from deepfix_rag.feedback import current_votes
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.interactions import Interaction, get_interaction, record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.tracing import trace_request

router = APIRouter(tags=["retrieval"])

INDEX_PREPARING = "The index is being prepared. Try again in a minute."
GENERATION_FAILED = "The answer could not be generated."


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
    return _stored_response(
        settings.product_db_path,
        kind="retrieval",
        user_id=user.id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=found[:limit],
    )


@router.post(
    "/ask",
    operation_id="ask_question",
    response_model=AskResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED, SERVICE_UNAVAILABLE),
    summary="Ask a question",
)
async def ask_question(payload: QueryRequest, request: Request) -> AskResponse:
    """Answer one question from the current index. Earlier questions are not included."""
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
        answered = await lease.service.answer(query)
        limit = displayed_limit(lease.service.config, settings.MAX_DISPLAYED_HITS)
        build_id = lease.build_id
        config_path = coordinator.snapshot().config_path
    finally:
        coordinator.release(lease)
    results = answered.retrieval_results[:limit]
    error = None if answered.generated_answer else GENERATION_FAILED
    stored = _store(
        settings.product_db_path,
        kind="ask",
        user_id=user.id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=results,
        answer=answered.generated_answer,
        error=error,
        trace_name="ask",
    )
    return AskResponse(
        interaction_id=stored.interaction_id,
        answer=stored.answer,
        error=stored.error,
        vote=_answer_vote(settings.product_db_path, user.id, stored.interaction_id),
        hits=_hit_responses(settings.product_db_path, stored, user.id),
    )


def _stored_response(
    db_path: Path,
    *,
    kind: str,
    user_id: str,
    query: str,
    build_id: str,
    config_path: str,
    results: list[RetrievalResult],
) -> RetrievalResponse:
    recorded = _store(
        db_path,
        kind=kind,
        user_id=user_id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=results,
        trace_name="retrieval",
    )
    return RetrievalResponse(
        interaction_id=recorded.interaction_id,
        hits=_hit_responses(db_path, recorded, user_id),
    )


def _store(
    db_path: Path,
    *,
    kind: str,
    user_id: str,
    query: str,
    build_id: str,
    config_path: str,
    results: list[RetrievalResult],
    answer: str | None = None,
    error: str | None = None,
    trace_name: str,
) -> Interaction:
    interaction_id = uuid.uuid4().hex
    trace_id = trace_request(
        trace_name,
        interaction_id,
        {"query": query},
        {
            "answer": answer,
            "hits": [
                {
                    "doc_id": result.doc_id,
                    "score": float(result.score or 0.0),
                    "text": result.text,
                }
                for result in results
            ],
        },
    )
    return record_interaction(
        db_path,
        kind=kind,
        user_id=user_id,
        query=query,
        build_id=build_id,
        config_path=config_path,
        results=results,
        answer=answer,
        error=error,
        trace_id=trace_id,
        interaction_id=interaction_id,
    )


def _hit_responses(db_path: Path, recorded: Interaction, user_id: str) -> list[HitResponse]:
    names = document_filenames(db_path, [hit.doc_id for hit in recorded.hits])
    _answer, passages = current_votes(db_path, user_id, recorded.interaction_id)
    return [
        HitResponse(
            rank=hit.rank,
            doc_id=hit.doc_id,
            document_filename=names.get(hit.doc_id, hit.doc_id),
            text=hit.text,
            score=hit.score,
            vote=passages.get(hit.rank),
        )
        for hit in recorded.hits
    ]


def _answer_vote(db_path: Path, user_id: str, interaction_id: str) -> str | None:
    answer, _passages = current_votes(db_path, user_id, interaction_id)
    return answer


@router.get(
    "/interactions/{interaction_id}",
    operation_id="get_interaction",
    response_model=InteractionResponse,
    responses=error_responses(UNAUTHORIZED, NOT_FOUND),
    summary="Read a stored search or ask",
)
def read_interaction(interaction_id: str, request: Request) -> InteractionResponse:
    """Return one interaction with the current user's votes. Used after a refresh."""
    user = current_user(request)
    settings = get_settings(request)
    stored = get_interaction(settings.product_db_path, interaction_id)
    if stored is None:
        raise HTTPException(status_code=NOT_FOUND, detail="No interaction with that id.")
    return InteractionResponse(
        interaction_id=stored.interaction_id,
        kind=cast(Literal["retrieval", "ask"], stored.kind),
        query=stored.query,
        answer=stored.answer,
        error=stored.error,
        vote=_answer_vote(settings.product_db_path, user.id, stored.interaction_id),
        hits=_hit_responses(settings.product_db_path, stored, user.id),
    )

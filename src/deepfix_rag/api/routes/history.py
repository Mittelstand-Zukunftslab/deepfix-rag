"""Admins review every search and ask with its answer and feedback."""

from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request

from deepfix_rag.api.deps import get_settings, require_admin
from deepfix_rag.api.errors import FORBIDDEN, NOT_FOUND, UNAUTHORIZED, error_responses
from deepfix_rag.api.schemas import (
    HistoryEntryResponse,
    HistoryPageResponse,
    InteractionReviewResponse,
    ReviewedHitResponse,
    VoteCountsResponse,
)
from deepfix_rag.history import (
    FeedbackFilter,
    HistoryEntry,
    VoteCounts,
    list_history,
    review_interaction,
)

router = APIRouter(tags=["history"])


def _votes(counts: VoteCounts) -> VoteCountsResponse:
    return VoteCountsResponse(up=counts.up, down=counts.down)


def _entry(entry: HistoryEntry) -> HistoryEntryResponse:
    return HistoryEntryResponse(
        interaction_id=entry.interaction_id,
        kind=cast(Literal["retrieval", "ask"], entry.kind),
        user_email=entry.user_email,
        query=entry.query,
        answer=entry.answer,
        error=entry.error,
        config_path=entry.config_path,
        created_at=entry.created_at,
        hit_count=entry.hit_count,
        answer_votes=_votes(entry.answer_votes),
        passage_votes=_votes(entry.passage_votes),
    )


@router.get(
    "/admin/interactions",
    operation_id="list_interaction_history",
    response_model=HistoryPageResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN),
    summary="List search and ask history",
)
def list_interaction_history(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    kind: Literal["retrieval", "ask"] | None = Query(default=None),
    feedback: FeedbackFilter = Query(default=FeedbackFilter.ALL),
    q: str | None = Query(default=None, max_length=200, description="Text in the query or email."),
) -> HistoryPageResponse:
    """Return every user's searches and asks, newest first. Members receive 403."""
    require_admin(request, "Only admins can review history.")
    page = list_history(
        get_settings(request).product_db_path,
        limit=limit,
        offset=offset,
        kind=kind,
        feedback=feedback,
        search=q,
    )
    return HistoryPageResponse(
        items=[_entry(entry) for entry in page.entries],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/admin/interactions/{interaction_id}",
    operation_id="review_interaction",
    response_model=InteractionReviewResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND),
    summary="Review one search or ask",
)
def review_interaction_route(interaction_id: str, request: Request) -> InteractionReviewResponse:
    """Return the passages shown, the answer, and the votes each received."""
    require_admin(request, "Only admins can review history.")
    review = review_interaction(get_settings(request).product_db_path, interaction_id)
    if review is None:
        raise HTTPException(status_code=NOT_FOUND, detail="No interaction with that id.")
    return InteractionReviewResponse(
        entry=_entry(review.entry),
        build_id=review.build_id,
        trace_id=review.trace_id,
        hits=[
            ReviewedHitResponse(
                rank=hit.rank,
                doc_id=hit.doc_id,
                document_filename=hit.document_filename,
                text=hit.text,
                score=hit.score,
                votes=_votes(hit.votes),
            )
            for hit in review.hits
        ],
    )

"""Vote on a shown passage or answer, and summarize feedback for admins."""

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import (
    BAD_REQUEST,
    FORBIDDEN,
    NOT_FOUND,
    UNAUTHORIZED,
    error_responses,
)
from deepfix_rag.api.schemas import (
    ConfigApprovalResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummaryResponse,
)
from deepfix_rag.feedback import (
    AnswerMissingError,
    UnknownInteractionError,
    UnknownPassageError,
    feedback_summary,
    record_vote,
)

router = APIRouter(tags=["feedback"])


@router.post(
    "/feedback",
    operation_id="submit_feedback",
    response_model=FeedbackResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED, NOT_FOUND),
    summary="Vote on a passage or an answer",
)
def submit_feedback(payload: FeedbackRequest, request: Request) -> FeedbackResponse:
    """Store one vote event and return. MLflow is updated later by the outbox."""
    user = current_user(request)
    settings = get_settings(request)
    try:
        vote = record_vote(
            settings.product_db_path,
            user_id=user.id,
            interaction_id=payload.interaction_id,
            target=payload.target,
            action=payload.action,
            rank=payload.rank,
        )
    except UnknownInteractionError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=str(exc)) from exc
    except (UnknownPassageError, AnswerMissingError) as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    return FeedbackResponse(
        interaction_id=payload.interaction_id,
        target=payload.target,
        rank=payload.rank if payload.target == "passage" else None,
        vote=vote,
    )


@router.get(
    "/admin/feedback/summary",
    operation_id="get_feedback_summary",
    response_model=FeedbackSummaryResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN),
    summary="Summarize feedback",
)
def get_feedback_summary(request: Request) -> FeedbackSummaryResponse:
    """Return vote, dataset, and outbox counts. Members receive 403."""
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=FORBIDDEN, detail="Only admins can view feedback.")
    summary = feedback_summary(get_settings(request).product_db_path)
    return FeedbackSummaryResponse(
        passage_up=summary.passage_up,
        passage_down=summary.passage_down,
        answer_up=summary.answer_up,
        answer_down=summary.answer_down,
        dev_question_count=summary.dev_question_count,
        holdout_question_count=summary.holdout_question_count,
        answer_approval_rate=summary.answer_approval_rate,
        answer_approval_by_config=[
            ConfigApprovalResponse(
                config_path=item.config_path,
                up=item.up,
                down=item.down,
                rate=item.rate,
            )
            for item in summary.answer_approval_by_config
        ],
        unsynced_event_count=summary.unsynced_event_count,
        no_trace_event_count=summary.no_trace_event_count,
        product_experiment_url=summary.product_experiment_url,
        feedback_experiment_url=summary.feedback_experiment_url,
    )

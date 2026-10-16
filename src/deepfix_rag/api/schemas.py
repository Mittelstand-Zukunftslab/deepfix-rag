"""Pydantic request and response models shared by public API routes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for response models.

    Fields with defaults are always present in a serialized response, so the schema marks them
    required and the generated TypeScript types stay non-optional.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ErrorResponse(ApiModel):
    """Body returned for every 4xx and 5xx response."""

    detail: str = Field(description="Human-readable explanation of what went wrong.")


class HealthResponse(ApiModel):
    """Liveness probe payload."""

    status: Literal["ok"] = "ok"


class IndexStatusResponse(ApiModel):
    """Overall state of the shared serving index."""

    status: Literal[
        "empty",
        "updating",
        "rebuilding",
        "ready",
        "ready_with_errors",
        "failed",
    ]
    document_count: int
    chunk_count: int
    pending_document_count: int
    updated_at: str | None = None
    config_path: str
    serving_build_id: str | None = None
    error: str | None = None


class TextIssueResponse(ApiModel):
    """A PDF page flagged as scanned or garbled. OCR is not run."""

    page: int
    kind: Literal["scanned", "garbled"]


class QueryRequest(ApiModel):
    """A search or ask question."""

    query: str = Field(min_length=1)


class HitResponse(ApiModel):
    """One passage shown for a search or an answer."""

    rank: int
    doc_id: str
    document_filename: str
    text: str
    score: float
    vote: Literal["up", "down"] | None = None


class RetrievalResponse(ApiModel):
    """Ranked passages for one search, with the interaction that recorded them."""

    interaction_id: str
    hits: list[HitResponse]


class AskResponse(ApiModel):
    """One single-turn answer and the passages it used."""

    interaction_id: str
    answer: str | None = None
    error: str | None = None
    vote: Literal["up", "down"] | None = None
    hits: list[HitResponse]


class InteractionResponse(ApiModel):
    """A stored search or ask, including the current user's votes."""

    interaction_id: str
    kind: Literal["retrieval", "ask"]
    query: str
    answer: str | None = None
    error: str | None = None
    vote: Literal["up", "down"] | None = None
    hits: list[HitResponse]


class FeedbackRequest(ApiModel):
    """A thumbs-up, thumbs-down, or clear on a passage or an answer."""

    interaction_id: str = Field(min_length=1)
    target: Literal["passage", "answer"]
    rank: int | None = Field(default=None, ge=1)
    action: Literal["up", "down", "clear"]


class FeedbackResponse(ApiModel):
    """The user's current vote after the event was stored."""

    interaction_id: str
    target: Literal["passage", "answer"]
    rank: int | None = None
    vote: Literal["up", "down"] | None = None


class ConfigApprovalResponse(ApiModel):
    """Answer approval for the interactions that used one champion config."""

    config_path: str
    up: int
    down: int
    rate: float | None = None


class FeedbackSummaryResponse(ApiModel):
    """Vote, dataset, and outbox counts for an admin."""

    passage_up: int
    passage_down: int
    answer_up: int
    answer_down: int
    dev_question_count: int
    holdout_question_count: int
    answer_approval_rate: float | None = None
    answer_approval_by_config: list[ConfigApprovalResponse]
    unsynced_event_count: int
    no_trace_event_count: int
    product_experiment_url: str | None = None
    feedback_experiment_url: str | None = None


class ConfigChange(ApiModel):
    """One field from a champion diff. ``before`` is the previous champion."""

    before: str | int | float | bool | None = None
    after: str | int | float | bool | None = None


class OptimizationRunSummary(ApiModel):
    """One stored optimization run, without holdout question text."""

    run_id: str
    status: Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
    trigger: Literal["cron", "manual"]
    reason: str | None = None
    primary_metric: str | None = None
    dev_delta: dict[str, float] | None = None
    holdout_delta: dict[str, float] | None = None
    holdout_wins: int | None = None
    holdout_losses: int | None = None
    mlflow_run_id: str | None = None
    mlflow_url: str | None = None
    started_at: str
    ended_at: str | None = None


class OptimizationStatusResponse(ApiModel):
    """Schedule, champion diff, and the 20 most recent runs."""

    cron: str
    timezone: str
    enabled: bool
    next_run_at: str | None = None
    agent_available: bool
    champion_config_path: str
    champion_diff: dict[str, ConfigChange]
    runs: list[OptimizationRunSummary]


class UpdateOptimizationRequest(ApiModel):
    """A five-field cron expression and whether the nightly job is enabled."""

    cron: str = Field(min_length=1)
    enabled: bool


class StartOptimizationRequest(ApiModel):
    """Start the optimization job now. The nightly schedule calls the same function."""


class OptimizationRunResponse(ApiModel):
    """The run recorded by a start request. Holdout questions are not included."""

    run_id: str
    status: Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
    trigger: Literal["cron", "manual"]
    reason: str | None = None
    dev_count: int
    new_dev_count: int
    holdout_count: int
    started_at: str
    ended_at: str | None = None


class DocumentResponse(ApiModel):
    """A non-deleted workspace document. Extracted text stays on disk."""

    doc_id: str
    filename: str
    media_type: str
    status: Literal["pending", "indexing", "indexed", "deleting", "failed"]
    index_error: str | None = None
    issues: list[TextIssueResponse] = Field(default_factory=list)
    uploader_email: str
    created_at: str
    can_delete: bool


class VoteCountsResponse(ApiModel):
    """Current thumbs up and down. A cleared vote no longer counts."""

    up: int = 0
    down: int = 0


class HistoryEntryResponse(ApiModel):
    """One search or ask in the admin history."""

    interaction_id: str
    kind: Literal["retrieval", "ask"]
    user_email: str = Field(description="Empty for interactions recorded before emails were kept.")
    query: str
    answer: str | None
    error: str | None
    config_path: str
    created_at: str
    hit_count: int
    answer_votes: VoteCountsResponse
    passage_votes: VoteCountsResponse


class HistoryPageResponse(ApiModel):
    """A page of history from every user, newest first."""

    items: list[HistoryEntryResponse]
    total: int
    limit: int
    offset: int


class ReviewedHitResponse(ApiModel):
    """A passage as it was shown, with the votes it received."""

    rank: int
    doc_id: str
    document_filename: str
    text: str
    score: float
    votes: VoteCountsResponse


class InteractionReviewResponse(ApiModel):
    """One interaction in full, for admin review."""

    entry: HistoryEntryResponse
    build_id: str
    trace_id: str | None
    hits: list[ReviewedHitResponse]

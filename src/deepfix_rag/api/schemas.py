"""Pydantic request and response models shared by public API routes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deepfix_rag.feedback import COMMENT_MAX_LENGTH, CommentReason
from deepfix_rag.grounding import Grounding


class ApiModel(BaseModel):
    """Base for response models.

    Fields with defaults are always present in a serialized response, so the schema marks them
    required and the generated TypeScript types stay non-optional.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ChatErrorDetail(ApiModel):
    """The chat routes' structured error body, nested under ``detail``.

    A chat failure carries a stable machine-readable ``code`` beside a sentence,
    so the UI branches on the code and an operator can tell "turned off"
    (``chat_disabled``) from "turned on but broken" (``chat_misconfigured``).
    Only the chat routes use this shape; every other route keeps ``detail: str``.
    """

    code: str = Field(description="Stable machine-readable reason, e.g. chat_disabled.")
    message: str = Field(description="Sentence shown to the admin.")


class ErrorResponse(ApiModel):
    """Body returned for every 4xx and 5xx response."""

    detail: str | ChatErrorDetail = Field(
        description=(
            "Human-readable explanation of what went wrong. The chat routes nest a "
            "{code, message} object here so the UI never matches on the sentence."
        )
    )


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
    """One single-turn answer and the passages it used.

    ``grounding`` is how the corpus supports the answer: ``grounded`` when the
    answer came from the retrieved passages, ``declined`` when the model refused
    because the corpus does not answer the question, and ``no_relevant_sources``
    when no passage passed the relevance floor and no answer was attempted. The
    UI branches on it, so it is never absent.
    """

    interaction_id: str
    answer: str | None = None
    error: str | None = None
    vote: Literal["up", "down"] | None = None
    grounding: Grounding
    hits: list[HitResponse]


class InteractionResponse(ApiModel):
    """A stored search or ask, including the current user's votes."""

    interaction_id: str
    kind: Literal["retrieval", "ask"]
    query: str
    answer: str | None = None
    error: str | None = None
    vote: Literal["up", "down"] | None = None
    grounding: Grounding
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


class FeedbackCommentRequest(ApiModel):
    """An optional comment on the user's current vote. Only admins can read it."""

    interaction_id: str = Field(min_length=1)
    target: Literal["passage", "answer"]
    rank: int | None = Field(default=None, ge=1)
    reason: CommentReason | None = Field(
        default=None, description="Only after a thumbs-down, from the target's reason list."
    )
    text: str = Field(default="", max_length=COMMENT_MAX_LENGTH)


class FeedbackCommentResponse(ApiModel):
    """The stored comment. The text is not echoed back."""

    comment_id: str
    created_at: str


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
    """Schedule, champion diff, and the 20 most recent runs.

    ``chat_enabled`` is the admin chat's switch (D-2). It rides here rather than
    on a chat route so the disabled page can render without calling one, and so
    the eleven chat routes stay exactly eleven.
    """

    chat_enabled: bool = False
    cron: str
    timezone: str
    enabled: bool
    next_run_at: str | None = None
    agent: Literal["hermes"] = "hermes"
    agent_available: bool
    agent_unavailable_message: str | None = None
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
    comment_count: int


class HistoryPageResponse(ApiModel):
    """A page of history from every user, newest first."""

    items: list[HistoryEntryResponse]
    total: int
    limit: int
    offset: int


class ReviewedCommentResponse(ApiModel):
    """A comment and the vote it was written for."""

    vote: Literal["up", "down"]
    reason: CommentReason | None
    text: str
    created_at: str


class ReviewedHitResponse(ApiModel):
    """A passage as it was shown, with the votes it received."""

    rank: int
    doc_id: str
    document_filename: str
    text: str
    score: float
    votes: VoteCountsResponse
    comments: list[ReviewedCommentResponse]


class InteractionReviewResponse(ApiModel):
    """One interaction in full, for admin review."""

    entry: HistoryEntryResponse
    build_id: str
    trace_id: str | None
    answer_comments: list[ReviewedCommentResponse]
    hits: list[ReviewedHitResponse]


class ChatMessageModel(ApiModel):
    """One message the browser sends. Only ``user`` and ``assistant`` are accepted."""

    role: Literal["user", "assistant"]
    content: str


class ChatCompletionRequest(ApiModel):
    """An OpenAI-shaped turn request.

    The server rebuilds the upstream conversation from the stored thread, so this
    array is a consistency check rather than the prompt: the client cannot rewrite
    the system message or claim a history that never happened.

    ``retry_of`` names the earlier turn whose answer is being asked for again.
    With ``regenerate`` the turn is reused and its answer replaced in place, which
    is what the chat UI's regenerate control does: the thread does not grow a
    second answer beside the first. Without it, a retry opens a new turn and the
    thread keeps both attempts (CHAT-005).
    """

    thread_id: str = Field(min_length=1)
    messages: list[ChatMessageModel] = Field(min_length=1)
    retry_of: str | None = None
    regenerate: bool = False


class ChatThreadResponse(ApiModel):
    """One conversation."""

    thread_id: str
    title: str
    created_at: str
    updated_at: str


class ChatThreadSummaryResponse(ChatThreadResponse):
    """A thread in the list: the same fields plus how its newest turn ended."""

    last_turn_status: Literal["running", "completed", "failed", "cancelled"] | None = None


class ChatMessageResponse(ApiModel):
    """One stored message."""

    message_id: str
    turn_id: str | None = None
    role: Literal["user", "assistant"]
    seq: int
    content: str
    created_at: str


class ChatTurnResponse(ApiModel):
    """One attempt at a reply."""

    turn_id: str
    status: Literal["running", "completed", "failed", "cancelled"]
    retry_of: str | None = None
    error: str | None = None
    first_token_ms: int | None = None
    started_at: str
    ended_at: str | None = None


class ChatActionResponse(ApiModel):
    """One proposed action card."""

    action_id: str
    turn_id: str
    kind: str
    payload: str = Field(description="The JSON object the assistant proposed, as text.")
    status: Literal["proposed", "confirmed", "rejected", "expired", "failed"]
    created_at: str
    expires_at: str
    decided_at: str | None = None
    result: str | None = None


class ChatThreadDetailResponse(ApiModel):
    """One thread with everything a page render needs."""

    thread: ChatThreadResponse
    messages: list[ChatMessageResponse]
    turns: list[ChatTurnResponse]
    actions: list[ChatActionResponse]


class ChatAuditResponse(ApiModel):
    """One audit row. It holds no message text, only ids, counts, and a detail."""

    event_id: int
    at: str
    admin_id: str
    admin_email: str
    thread_id: str | None = None
    turn_id: str | None = None
    kind: str
    detail: str
    thread_deleted: bool = Field(
        description="True when the thread this event names has since been deleted."
    )


class ChatThreadRunResponse(ApiModel):
    """One optimization run linked to a thread, with its step tail."""

    run_id: str
    status: Literal["running", "skipped", "rejected", "promoted", "succeeded", "failed"]
    trigger: Literal["cron", "manual"]
    reason: str | None = None
    started_at: str
    ended_at: str | None = None
    primary_metric: str | None = None
    dev_delta: dict[str, float] | None = None
    holdout_delta: dict[str, float] | None = None
    holdout_wins: int | None = None
    holdout_losses: int | None = None
    mlflow_run_id: str | None = None
    mlflow_url: str | None = None
    steps: list[str] = Field(default_factory=list)


class AskChatCitationResponse(ApiModel):
    """One ``[n]`` marker in an answer and the passage it resolved to."""

    marker: int = Field(description="The number inside the marker, as the member sees it.")
    rank: int = Field(description="The passage's rank in that turn's tool output.")
    doc_id: str


class AskChatPassageResponse(ApiModel):
    """One stored passage: the hit snapshot a turn's answer was built from.

    The text is the whole chunk, not the truncated copy sent to the model, so a
    reload shows what the vote records under that rank (FR-006).
    """

    rank: int
    doc_id: str
    filename: str
    text: str
    score: float
    truncated: bool = False


class AskChatThreadResponse(ApiModel):
    """One of the member's conversations."""

    thread_id: str
    title: str
    created_at: str
    updated_at: str


class AskChatRenameRequest(ApiModel):
    """A new thread title.

    The bound is on the **trimmed** title, and the store is the one that applies
    it: whitespace is collapsed and stripped, and only then is the length checked
    against 120. That is why this field's `max_length` is a payload guard rather
    than the rule — a title of exactly 120 characters arriving with trailing
    spaces is a valid rename, and a field that measured the raw string would
    refuse it. `min_length` is 1 so an entirely empty body field is still a
    validation error; a whitespace-only title passes here and is refused by the
    store as empty-after-trimming, which is the same 400 either way.
    """

    title: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "The new title. Leading and trailing whitespace is removed before the "
            "1 to 120 character limit is applied."
        ),
    )


class AskChatTurnResponse(ApiModel):
    """One question and the answer stored for it.

    ``interaction_id``, ``question`` and ``answer`` are null until the turn's
    ``interactions`` row exists, which is how a turn still ``running`` reports.
    ``passages`` is that turn's stored hit snapshot, which is what lets a reload
    rebuild the sources panel without a second request per answer (FR-007).
    """

    turn_id: str
    seq: int
    status: Literal["running", "completed", "failed", "cancelled"]
    retry_of: str | None = None
    error: str | None = None
    grounding: Grounding | None = None
    citations: list[AskChatCitationResponse] = Field(default_factory=list)
    passages: list[AskChatPassageResponse] = Field(default_factory=list)
    retrieval_query: str | None = None
    first_token_ms: int | None = None
    started_at: str
    ended_at: str | None = None
    interaction_id: str | None = Field(
        default=None,
        description="The interactions row that holds this turn's question and answer.",
    )
    question: str | None = None
    answer: str | None = None


class AskChatThreadDetailResponse(ApiModel):
    """A thread and its turns in order, with each turn's grounding and citations.

    One load is enough to draw the whole conversation, including the badges, so
    a reload does not issue a request per turn (FR-010).
    """

    thread: AskChatThreadResponse
    turns: list[AskChatTurnResponse]


class AskChatCompletionRequest(ApiModel):
    """One Ask chat turn.

    ``messages`` carries the browser's view for the AI SDK's own bookkeeping; the
    prompt is rebuilt from the store, so the array is not trusted and only its
    last entry's role is checked (FR-011). ``regenerate`` marks the AI SDK's
    reload trigger, which replaces the newest answered turn in place (FR-015).
    """

    thread_id: str = Field(min_length=1)
    messages: list[ChatMessageModel] = Field(min_length=1)
    regenerate: bool = False


class LegacyTurnRequest(ApiModel):
    """One turn from the browser's old localStorage sessions."""

    interaction_id: str | None = Field(default=None, alias="interactionId")
    user_query: str = Field(default="", alias="userQuery")
    answer: str | None = None

    model_config = ConfigDict(
        populate_by_name=True, json_schema_serialization_defaults_required=True
    )


class LegacySessionRequest(ApiModel):
    """One session from the browser's old localStorage sessions."""

    id: str = Field(min_length=1)
    title: str = ""
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    turns: list[LegacyTurnRequest] = Field(default_factory=list)

    model_config = ConfigDict(
        populate_by_name=True, json_schema_serialization_defaults_required=True
    )


class AskChatImportRequest(ApiModel):
    """The browser's legacy sessions, imported once (FR-033).

    The list is bounded rather than unbounded: an import is one write per stored
    turn, so a payload past the bound is a 400 naming it instead of a long write.
    An empty list is accepted and creates nothing, which is what a member with
    nothing to import produces.
    """

    sessions: list[LegacySessionRequest] = Field(default_factory=list, max_length=200)


class AskChatImportResponse(ApiModel):
    """What the import created and what it skipped, so the client can say so."""

    threads_created: int
    turns_created: int
    turns_skipped: int

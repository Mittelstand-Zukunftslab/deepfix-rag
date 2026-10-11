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


class UserResponse(ApiModel):
    """A signed-in account. The password hash is never included."""

    id: str
    email: str
    role: Literal["member", "admin"]


class CredentialsRequest(ApiModel):
    """Email and password for register and login."""

    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RoleUpdateRequest(ApiModel):
    """New role for an existing account."""

    role: Literal["member", "admin"]


class LogoutResponse(ApiModel):
    """Confirmation that the session cookie was cleared."""

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

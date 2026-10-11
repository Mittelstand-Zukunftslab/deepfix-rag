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

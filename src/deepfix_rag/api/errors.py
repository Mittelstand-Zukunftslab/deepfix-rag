"""Shared error-response declarations so every status code appears in the OpenAPI schema."""

from typing import Any

from deepfix_rag.api.schemas import ErrorResponse

BAD_REQUEST = 400
UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
CONFLICT = 409
SERVICE_UNAVAILABLE = 503

_DESCRIPTIONS: dict[int, str] = {
    BAD_REQUEST: "Invalid input.",
    UNAUTHORIZED: "No valid session.",
    FORBIDDEN: "The signed-in user's role does not allow this action.",
    NOT_FOUND: "Unknown id.",
    CONFLICT: "The request conflicts with the current state.",
    SERVICE_UNAVAILABLE: "A required dependency is not ready.",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build a FastAPI ``responses`` mapping that documents ``ErrorResponse`` per status code."""
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in status_codes
    }

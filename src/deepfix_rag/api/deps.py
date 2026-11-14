"""Request dependencies for the product API."""

from fastapi import HTTPException, Request

from deepfix_rag.api.errors import FORBIDDEN, SERVICE_UNAVAILABLE, UNAUTHORIZED
from deepfix_rag.api.identity import (
    IdentityUnavailableError,
    InvalidTokenError,
    TokenVerifier,
    User,
)
from deepfix_rag.config.settings import Settings

_BEARER = "bearer "


def get_settings(request: Request) -> Settings:
    """Return the settings bound to this app instance."""
    return request.app.state.settings


def current_user(request: Request) -> User:
    """Resolve the web app's bearer token, or 401 when there is no valid token."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER):
        raise HTTPException(status_code=UNAUTHORIZED, detail="Sign in to continue.")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return verifier.verify(header[len(_BEARER) :].strip())
    except InvalidTokenError as exc:
        raise HTTPException(status_code=UNAUTHORIZED, detail="Sign in to continue.") from exc
    except IdentityUnavailableError as exc:
        raise HTTPException(status_code=SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def require_admin(request: Request, detail: str = "Only admins can do this.") -> User:
    """Allow the request only for an admin. The web app checks first; this is the backstop."""
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=FORBIDDEN, detail=detail)
    return user

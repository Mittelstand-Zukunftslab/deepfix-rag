"""Request dependencies for the product API."""

from fastapi import HTTPException, Request

from deepfix_rag.accounts import COOKIE_NAME, User, user_for_session
from deepfix_rag.api.errors import FORBIDDEN, UNAUTHORIZED
from deepfix_rag.config.settings import Settings


def get_settings(request: Request) -> Settings:
    """Return the settings bound to this app instance."""
    return request.app.state.settings


def current_user(request: Request) -> User:
    """Resolve the session cookie, or 401 when there is no valid session."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=UNAUTHORIZED, detail="Sign in to continue.")
    user = user_for_session(get_settings(request).product_db_path, token)
    if user is None:
        raise HTTPException(status_code=UNAUTHORIZED, detail="Sign in to continue.")
    return user


def require_admin(request: Request) -> User:
    """Allow the request only for an admin session."""
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=FORBIDDEN, detail="Only admins can change roles.")
    return user

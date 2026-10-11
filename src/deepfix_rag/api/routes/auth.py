"""Register, sign in, sign out, and the current user."""

from fastapi import APIRouter, HTTPException, Request, Response

from deepfix_rag.accounts import (
    COOKIE_NAME,
    SESSION_DAYS,
    DuplicateEmailError,
    EmailDomainError,
    InvalidEmailError,
    InvalidPasswordError,
    User,
    authenticate,
    create_session,
    delete_session,
    register_member,
)
from deepfix_rag.api.deps import current_user, get_settings
from deepfix_rag.api.errors import BAD_REQUEST, CONFLICT, UNAUTHORIZED, error_responses
from deepfix_rag.api.schemas import CredentialsRequest, LogoutResponse, UserResponse

router = APIRouter(tags=["auth"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, role=user.role)


def _secure_cookie(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    proto = forwarded.split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
    )


@router.post(
    "/auth/register",
    operation_id="register",
    response_model=UserResponse,
    status_code=201,
    responses=error_responses(BAD_REQUEST, CONFLICT),
    summary="Create a member account",
)
def register(payload: CredentialsRequest, request: Request) -> UserResponse:
    """Create a member. This route never creates an admin."""
    settings = get_settings(request)
    try:
        user = register_member(
            settings.product_db_path,
            payload.email,
            payload.password,
            settings.allowed_email_domains,
        )
    except (InvalidEmailError, InvalidPasswordError, EmailDomainError) as exc:
        raise HTTPException(status_code=BAD_REQUEST, detail=str(exc)) from exc
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc
    return _user_response(user)


@router.post(
    "/auth/login",
    operation_id="login",
    response_model=UserResponse,
    responses=error_responses(BAD_REQUEST, UNAUTHORIZED),
    summary="Sign in",
)
def login(payload: CredentialsRequest, request: Request, response: Response) -> UserResponse:
    """Set the session cookie when the email and password match."""
    settings = get_settings(request)
    user = authenticate(settings.product_db_path, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=UNAUTHORIZED, detail="Email or password is incorrect.")
    token, _expires = create_session(settings.product_db_path, user.id)
    _set_session_cookie(response, request, token)
    return _user_response(user)


@router.post(
    "/auth/logout",
    operation_id="logout",
    response_model=LogoutResponse,
    summary="Sign out",
)
def logout(request: Request, response: Response) -> LogoutResponse:
    """Clear the session cookie. Signing out with no session still succeeds."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(get_settings(request).product_db_path, token)
    _clear_session_cookie(response, request)
    return LogoutResponse()


@router.get(
    "/auth/me",
    operation_id="me",
    response_model=UserResponse,
    responses=error_responses(UNAUTHORIZED),
    summary="Current user",
)
def me(request: Request) -> UserResponse:
    """Return the id, email, and role for the session cookie."""
    return _user_response(current_user(request))

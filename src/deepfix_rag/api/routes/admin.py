"""Admin-only account routes."""

from fastapi import APIRouter, HTTPException, Request

from deepfix_rag.accounts import LastAdminError, UnknownUserError, set_user_role
from deepfix_rag.api.deps import get_settings, require_admin
from deepfix_rag.api.errors import CONFLICT, FORBIDDEN, NOT_FOUND, UNAUTHORIZED, error_responses
from deepfix_rag.api.schemas import RoleUpdateRequest, UserResponse

router = APIRouter(tags=["admin"])


@router.patch(
    "/admin/users/{user_id}",
    operation_id="update_user_role",
    response_model=UserResponse,
    responses=error_responses(UNAUTHORIZED, FORBIDDEN, NOT_FOUND, CONFLICT),
    summary="Change a user's role",
)
def update_user_role(user_id: str, payload: RoleUpdateRequest, request: Request) -> UserResponse:
    """Promote or demote an account. Members receive 403. The last admin cannot be demoted."""
    require_admin(request)
    try:
        user = set_user_role(get_settings(request).product_db_path, user_id, payload.role)
    except UnknownUserError as exc:
        raise HTTPException(status_code=NOT_FOUND, detail=str(exc)) from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc
    return UserResponse(id=user.id, email=user.email, role=user.role)

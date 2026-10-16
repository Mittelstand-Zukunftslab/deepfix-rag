"""Verify the short-lived identity token the web server attaches to every API call."""

from dataclasses import dataclass
from typing import Any, Literal

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKSet

Role = Literal["member", "admin"]


class InvalidTokenError(Exception):
    """The bearer token is missing, expired, or not signed by the web app."""


class IdentityUnavailableError(Exception):
    """The web app's signing keys could not be fetched."""


@dataclass(frozen=True)
class User:
    """The caller, as vouched for by the web app."""

    id: str
    email: str
    role: Role


class TokenVerifier:
    """Check Better Auth JWTs against the web app's JWKS.

    Pass ``jwks_url`` to fetch and cache keys from the web app, or ``jwks`` to pin a key set.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        jwks: dict[str, Any] | None = None,
        algorithms: tuple[str, ...] = ("EdDSA",),
    ) -> None:
        if (jwks_url is None) == (jwks is None):
            raise ValueError("Pass exactly one of jwks_url or jwks.")
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)
        self._client = PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None
        self._keys = PyJWKSet.from_dict(jwks) if jwks else None

    def verify(self, token: str) -> User:
        """Return the user named by a valid token."""
        try:
            claims = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "sub"]},
            )
        except PyJWKClientConnectionError as exc:
            raise IdentityUnavailableError("The sign-in service is unavailable.") from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc
        return _user_from_claims(claims)

    def _signing_key(self, token: str) -> Any:
        if self._client is not None:
            return self._client.get_signing_key_from_jwt(token).key
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self._keys.keys if self._keys else []:
            if kid is None or key.key_id == kid:
                return key.key
        raise jwt.InvalidKeyError("No signing key matches the token.")


def _user_from_claims(claims: dict[str, Any]) -> User:
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise InvalidTokenError("The token has no email.")
    # Better Auth stores multiple roles as a comma-separated string.
    roles = {part.strip() for part in str(claims.get("role") or "").split(",")}
    role: Role = "admin" if "admin" in roles else "member"
    return User(id=str(claims["sub"]), email=email, role=role)

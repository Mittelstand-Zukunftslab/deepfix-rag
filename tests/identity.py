"""Identity tokens for API tests, signed like Better Auth's JWT plugin signs them."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import jwt

from deepfix_rag.api.identity import TokenVerifier

WEB_ORIGIN = "http://localhost:8080"


@dataclass(frozen=True)
class IdentityIssuer:
    """Signs identity tokens the way Better Auth's JWT plugin does, with a real Ed25519 key."""

    private_key: Ed25519PrivateKey
    kid: str
    jwks: dict[str, Any]

    def verifier(self) -> TokenVerifier:
        return TokenVerifier(issuer=WEB_ORIGIN, audience=WEB_ORIGIN, jwks=self.jwks)

    @staticmethod
    def user_id(email: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"mailto:{email.lower()}").hex

    def token(
        self,
        email: str,
        role: Literal["member", "admin"] = "member",
        *,
        expires_in: timedelta = timedelta(minutes=15),
        **claims: Any,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": self.user_id(email),
            "id": self.user_id(email),
            "email": email,
            "role": role,
            "iss": WEB_ORIGIN,
            "aud": WEB_ORIGIN,
            "iat": now,
            "exp": now + expires_in,
            **claims,
        }
        return jwt.encode(payload, self.private_key, algorithm="EdDSA", headers={"kid": self.kid})

    def sign_in(
        self, client: TestClient, email: str, role: Literal["member", "admin"] = "member"
    ) -> str:
        """Act as ``email`` on every following request from ``client``. Returns the user id."""
        client.headers["Authorization"] = f"Bearer {self.token(email, role)}"
        return self.user_id(email)

    @staticmethod
    def sign_out(client: TestClient) -> None:
        client.headers.pop("Authorization", None)

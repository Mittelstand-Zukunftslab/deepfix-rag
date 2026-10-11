"""Product accounts and sessions stored in the API-private SQLite database."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Iterator, Literal
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

SESSION_DAYS = 14
COOKIE_NAME = "deepfix_session"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()


class DuplicateEmailError(Exception):
    """An account with this email already exists."""


class EmailDomainError(Exception):
    """The email domain is not allowed to register."""

    def __init__(self, domains: list[str]) -> None:
        self.domains = domains
        super().__init__(domain_message(domains))


class InvalidEmailError(Exception):
    """The email address is not usable."""


class InvalidPasswordError(Exception):
    """The password does not meet the minimum length."""


class UnknownUserError(Exception):
    """No account has this id."""


class LastAdminError(Exception):
    """Demoting this user would leave the workspace with no admin."""


@dataclass(frozen=True)
class User:
    """A signed-in account. The password hash never leaves this module."""

    id: str
    email: str
    role: Literal["member", "admin"]
    password_hash: str


def domain_message(domains: list[str]) -> str:
    """The exact registration error for a closed list of domains."""
    return f"Registration is limited to {', '.join(domains)} addresses."


def normalize_email(email: str) -> str:
    """Return a lowercase email, or raise when it is not an address."""
    normalized = email.strip().lower()
    if not _EMAIL.fullmatch(normalized):
        raise InvalidEmailError("Enter a valid email address.")
    return normalized


def validate_password(password: str) -> str:
    """Reject passwords shorter than 8 characters."""
    if len(password) < 8:
        raise InvalidPasswordError("Use at least 8 characters.")
    return password


def hash_password(password: str) -> str:
    """Store only an Argon2 hash."""
    return _hasher.hash(validate_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether ``password`` matches the stored Argon2 hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def init_db(db_path: Path) -> None:
    """Create the users and sessions tables if they do not exist."""
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('member', 'admin')),
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )


def register_member(
    db_path: Path,
    email: str,
    password: str,
    allowed_domains: list[str] | None = None,
) -> User:
    """Create a member. Registration cannot create an admin."""
    normalized = normalize_email(email)
    domains = [domain.lower() for domain in (allowed_domains or [])]
    if domains:
        host = normalized.rsplit("@", 1)[-1]
        if host not in domains:
            raise EmailDomainError(domains)
    return _insert_user(db_path, normalized, password, "member")


def create_admin(db_path: Path, email: str, password: str) -> User:
    """Create an admin from the server-side management command."""
    return _insert_user(db_path, normalize_email(email), password, "admin")


def authenticate(db_path: Path, email: str, password: str) -> User | None:
    """Return the user when the email and password match."""
    try:
        normalized = normalize_email(email)
    except InvalidEmailError:
        return None
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (normalized,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return _user_from_row(row)


def create_session(db_path: Path, user_id: str) -> tuple[str, datetime]:
    """Open a session that expires 14 days after it is created. Returns the raw token."""
    token = secrets.token_urlsafe(32)
    created = _now()
    expires = created + timedelta(days=SESSION_DAYS)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (_token_hash(token), user_id, _iso(created), _iso(expires)),
        )
    return token, expires


def user_for_session(db_path: Path, token: str) -> User | None:
    """Resolve a session token. Expired sessions are removed and treated as signed out."""
    now = _iso(_now())
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT users.*, sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
            """,
            (_token_hash(token),),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            return None
    return _user_from_row(row)


def delete_session(db_path: Path, token: str) -> None:
    """Forget a session token. Missing tokens are ignored."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def set_user_role(db_path: Path, user_id: str, role: Literal["member", "admin"]) -> User:
    """Change a role. Demoting the last admin is refused."""
    if role not in {"admin", "member"}:
        raise ValueError("Role must be admin or member.")
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise UnknownUserError("No user with that id.")
        if row["role"] == "admin" and role == "member":
            admins = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
            ).fetchone()["n"]
            if admins <= 1:
                raise LastAdminError("The workspace needs at least one admin.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_from_row(updated)


def session_bounds(db_path: Path, token: str) -> tuple[datetime, datetime] | None:
    """Return the session's creation and expiry times."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT created_at, expires_at FROM sessions WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["expires_at"])


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open SQLite, commit on success, and always close the connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_user(
    db_path: Path, email: str, password: str, role: Literal["member", "admin"]
) -> User:
    init_db(db_path)
    user = User(
        id=uuid.uuid4().hex,
        email=email,
        role=role,
        password_hash=hash_password(password),
    )
    try:
        with connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user.id, user.email, user.password_hash, user.role, _iso(_now())),
            )
    except sqlite3.IntegrityError as exc:
        raise DuplicateEmailError("An account with this email already exists.") from exc
    return user


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        password_hash=row["password_hash"],
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()

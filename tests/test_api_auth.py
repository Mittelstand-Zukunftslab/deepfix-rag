"""Accounts, sessions, and role changes."""

from datetime import timedelta
import json
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from deepfix_rag.accounts import SESSION_DAYS, connect, create_admin, session_bounds
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings

pytestmark = [pytest.mark.api, pytest.mark.unit]


@pytest.fixture
def auth_settings(tmp_path):
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        ALLOWED_EMAIL_DOMAINS="example.com, hpi.de",
        WEB_ORIGIN="http://localhost:8080",
    )


@pytest.fixture
def client(auth_settings: Settings) -> TestClient:
    return TestClient(create_app(auth_settings))


def test_register_login_me_and_logout(client: TestClient, auth_settings: Settings) -> None:
    created = client.post(
        "/auth/register",
        json={"email": "Member@Example.com", "password": "correct-horse"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["role"] == "member"
    assert body["email"] == "member@example.com"
    assert "password" not in body
    assert "hash" not in json.dumps(body)

    signed_in = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "correct-horse"},
    )
    assert signed_in.status_code == 200
    cookie = signed_in.headers["set-cookie"]
    assert "deepfix_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "member@example.com"

    secure = client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "correct-horse"},
        headers={"x-forwarded-proto": "https"},
    )
    assert "Secure" in secure.headers["set-cookie"]

    logged_out = client.post("/auth/logout")
    assert logged_out.status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_registration_rules(client: TestClient) -> None:
    short = client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert short.status_code == 400
    assert short.json()["detail"] == "Use at least 8 characters."

    other_domain = client.post(
        "/auth/register",
        json={"email": "a@elsewhere.org", "password": "correct-horse"},
    )
    assert other_domain.status_code == 400
    assert other_domain.json()["detail"] == (
        "Registration is limited to example.com, hpi.de addresses."
    )

    client.post("/auth/register", json={"email": "a@example.com", "password": "correct-horse"})
    duplicate = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "correct-horse"},
    )
    assert duplicate.status_code == 409
    wrong = client.post(
        "/auth/login",
        json={"email": "a@example.com", "password": "not-the-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Email or password is incorrect."


def test_sessions_expire_after_fourteen_days(client: TestClient, auth_settings: Settings) -> None:
    client.post("/auth/register", json={"email": "a@example.com", "password": "correct-horse"})
    signed_in = client.post(
        "/auth/login",
        json={"email": "a@example.com", "password": "correct-horse"},
    )
    token = signed_in.cookies["deepfix_session"]
    created, expires = session_bounds(auth_settings.product_db_path, token)
    assert expires - created == timedelta(days=SESSION_DAYS)

    with connect(auth_settings.product_db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert client.get("/auth/me").status_code == 401


def test_role_changes_are_admin_only(client: TestClient, auth_settings: Settings) -> None:
    admin = create_admin(auth_settings.product_db_path, "admin@example.com", "correct-horse")
    member = client.post(
        "/auth/register",
        json={"email": "member@example.com", "password": "correct-horse"},
    ).json()

    client.post(
        "/auth/login",
        json={"email": "member@example.com", "password": "correct-horse"},
    )
    forbidden = client.patch(f"/admin/users/{admin.id}", json={"role": "member"})
    assert forbidden.status_code == 403

    admin_client = TestClient(client.app)
    admin_client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "correct-horse"},
    )
    missing = admin_client.patch("/admin/users/missing", json={"role": "admin"})
    assert missing.status_code == 404

    promoted = admin_client.patch(f"/admin/users/{member['id']}", json={"role": "admin"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    demoted = admin_client.patch(f"/admin/users/{member['id']}", json={"role": "member"})
    assert demoted.status_code == 200

    last = admin_client.patch(f"/admin/users/{admin.id}", json={"role": "member"})
    assert last.status_code == 409
    assert last.json()["detail"] == "The workspace needs at least one admin."

    anonymous = TestClient(client.app)
    assert anonymous.patch(f"/admin/users/{admin.id}", json={"role": "member"}).status_code == 401


def test_register_never_creates_an_admin(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "correct-horse", "role": "admin"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "member"


def test_create_admin_command_is_not_part_of_the_deepfix_cli(cli_runner) -> None:
    result = cli_runner.invoke(app_cli(), ["create-admin", "--help"])
    assert result.exit_code != 0


def test_create_admin_reads_the_password_from_stdin(tmp_path) -> None:
    data_dir = tmp_path / "private"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepfix_rag.api.manage",
            "create-admin",
            "--email",
            "root@example.com",
        ],
        input="correct-horse\n",
        text=True,
        capture_output=True,
        env={**_env(), "PRODUCT_DATA_DIR": str(data_dir)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Created admin root@example.com." in completed.stdout

    short = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepfix_rag.api.manage",
            "create-admin",
            "--email",
            "other@example.com",
        ],
        input="short\n",
        text=True,
        capture_output=True,
        env={**_env(), "PRODUCT_DATA_DIR": str(data_dir)},
        check=False,
    )
    assert short.returncode == 1
    assert "Use at least 8 characters." in short.stderr


def app_cli():
    from deepfix_rag.main import app

    return app


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)

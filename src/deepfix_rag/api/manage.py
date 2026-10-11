"""Server-side account commands. This module is not part of the deepfix CLI.

Usage: ``uv run python -m deepfix_rag.api.manage create-admin --email <email>``

The password is read from standard input.
"""

import argparse
import sys

from deepfix_rag.accounts import (
    DuplicateEmailError,
    InvalidEmailError,
    InvalidPasswordError,
    create_admin,
)
from deepfix_rag.config.settings import settings


def main(argv: list[str] | None = None) -> None:
    """Dispatch a management command."""
    parser = argparse.ArgumentParser(prog="python -m deepfix_rag.api.manage")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create-admin", help="Create an admin account.")
    create.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        _create_admin(args.email)


def _create_admin(email: str) -> None:
    password = sys.stdin.readline()
    if password.endswith("\n"):
        password = password[:-1]
    if password.endswith("\r"):
        password = password[:-1]
    try:
        user = create_admin(settings.product_db_path, email, password)
    except (InvalidEmailError, InvalidPasswordError, DuplicateEmailError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Created admin {user.email}.")


if __name__ == "__main__":
    main()

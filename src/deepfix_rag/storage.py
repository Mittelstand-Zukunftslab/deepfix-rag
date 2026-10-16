"""The API-private SQLite database shared by documents, interactions, feedback, and runs."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3

_USER_REFERENCE = " REFERENCES users(id)"
_USER_TABLES = ("documents", "interactions")


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


def drop_account_tables(db_path: Path) -> None:
    """Remove the users and sessions tables left by FastAPI-managed sign-in.

    Accounts now live in the web app. Rows that pointed at ``users`` keep their ids, and each
    document keeps its uploader's email in ``uploader_email``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # Must be set outside a transaction; the rebuild drops tables other rows point at.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                row["name"]: row["sql"]
                for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
            }
            if "documents" in tables and "uploader_email" not in _columns(conn, "documents"):
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN uploader_email TEXT NOT NULL DEFAULT ''"
                )
                if "users" in tables:
                    conn.execute(
                        """
                        UPDATE documents
                        SET uploader_email = (
                            SELECT email FROM users WHERE users.id = documents.uploaded_by
                        )
                        WHERE EXISTS (SELECT 1 FROM users WHERE users.id = documents.uploaded_by)
                        """
                    )
            for table in _USER_TABLES:
                sql = tables.get(table)
                if sql and _USER_REFERENCE in sql:
                    _rebuild_without_user_reference(conn, table)
            conn.execute("DROP TABLE IF EXISTS sessions")
            conn.execute("DROP TABLE IF EXISTS users")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _rebuild_without_user_reference(conn: sqlite3.Connection, table: str) -> None:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()["sql"]
    staged = f"{table}__rebuild"
    new_sql = re.sub(
        rf"^CREATE TABLE\s+\"?{table}\"?",
        f"CREATE TABLE {staged}",
        sql.replace(_USER_REFERENCE, ""),
        count=1,
    )
    columns = ", ".join(_columns(conn, table))
    conn.execute(f"DROP TABLE IF EXISTS {staged}")
    conn.execute(new_sql)
    conn.execute(f"INSERT INTO {staged} ({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {staged} RENAME TO {table}")

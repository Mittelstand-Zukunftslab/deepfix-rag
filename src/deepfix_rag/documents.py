"""Shared workspace documents, corpus files, and the champion config seed."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Literal
import uuid

from pydantic import BaseModel, Field, ValidationError

from deepfix_rag.accounts import connect
from deepfix_rag.config.schema import DataSourceType, RAGConfig

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf"}
_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
}
_KNOWN_REASONS = frozenset(
    {
        "scanned",
        "no-text",
        "sparse-text",
        "embedded-images",
        "garbled",
        "vector-text",
        "annotation-text",
    }
)
_HIGHLIGHT_REASONS = frozenset({"scanned", "garbled"})
_LAYOUT = ("corpus", "indexes", "feedback", "configs", "runs")

DocumentStatus = Literal["pending", "indexing", "indexed", "deleting", "failed"]
IssueKind = Literal["scanned", "garbled"]

SCANNED_EMPTY = "No extractable text. This PDF looks scanned, and OCR is not available."
GARBLED_EMPTY = "No extractable text. The text layer is garbled, and OCR is not available."
EMPTY_PDF = "No extractable text. Scanned PDFs are not supported."


class UploadRejected(Exception):
    """The upload cannot be stored."""


class UnknownDocumentError(Exception):
    """No visible document has this id."""

    def __init__(self) -> None:
        super().__init__("No document with that id.")


class DeleteForbiddenError(Exception):
    """A member tried to delete someone else's file."""

    def __init__(self) -> None:
        super().__init__("You can only delete files you uploaded.")


@dataclass(frozen=True)
class TextIssue:
    """A page whose extracted text is scanned or garbled."""

    page: int
    kind: IssueKind


@dataclass(frozen=True)
class DocumentRecord:
    """One shared-corpus file, including soft-deleted rows."""

    doc_id: str
    filename: str
    media_type: str
    text: str
    status: DocumentStatus
    index_error: str | None
    issues: list[TextIssue]
    uploaded_by: str
    uploader_email: str
    created_at: str
    indexed_at: str | None
    deleted_at: str | None
    can_delete: bool


class _PageReading(BaseModel):
    page: int = Field(ge=1)
    reasons: list[str]


class _PdfReading(BaseModel):
    text: str
    pages: list[_PageReading]


def default_config_path() -> Path:
    """Repo default used to seed the champion config."""
    return Path(__file__).resolve().parents[2] / "configs" / "default.yaml"


def init_documents(db_path: Path) -> None:
    """Create the documents table if it does not exist."""
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('pending', 'indexing', 'indexed', 'deleting', 'failed')
                ),
                index_error TEXT,
                text_issues TEXT NOT NULL DEFAULT '[]',
                uploaded_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                indexed_at TEXT,
                deleted_at TEXT
            )
            """
        )


def prepare_workspace(workspace: Path, default_config: Path | None = None) -> Path:
    """Create the workspace layout and seed the champion config once."""
    for name in _LAYOUT:
        (workspace / name).mkdir(parents=True, exist_ok=True)
    (workspace / "indexes" / "current").mkdir(parents=True, exist_ok=True)
    return _seed_champion(workspace, default_config or default_config_path())


def add_document(
    db_path: Path,
    workspace: Path,
    *,
    user_id: str,
    filename: str | None,
    content: bytes,
    reading: str | None,
) -> DocumentRecord:
    """Validate one upload, store the row, and write corpus JSON when text exists."""
    init_documents(db_path)
    prepare_workspace(workspace)
    stored_name, suffix = _filename(filename)
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected("Files must be 20 MB or smaller.")

    issues: list[TextIssue] = []
    if suffix == ".pdf":
        text, issues, status, index_error = _read_pdf(content, reading)
    else:
        text = _decode_text(content)
        status, index_error = "pending", None

    doc_id = f"ws_{uuid.uuid4().hex}"
    created_at = _iso(_now())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, filename, media_type, text, status, index_error, text_issues,
                uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                stored_name,
                _MEDIA_TYPES[suffix],
                text,
                status,
                index_error,
                _dump_issues(issues),
                user_id,
                created_at,
            ),
        )
        email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if email is None:
        raise UploadRejected("Sign in to continue.")
    if status != "failed":
        _write_corpus(workspace, doc_id, text, stored_name)
    return DocumentRecord(
        doc_id=doc_id,
        filename=stored_name,
        media_type=_MEDIA_TYPES[suffix],
        text=text,
        status=status,
        index_error=index_error,
        issues=issues,
        uploaded_by=user_id,
        uploader_email=email["email"],
        created_at=created_at,
        indexed_at=None,
        deleted_at=None,
        can_delete=True,
    )


def list_documents(
    db_path: Path, workspace: Path, user_id: str, role: str
) -> list[DocumentRecord]:
    """Return non-deleted documents, newest first."""
    init_documents(db_path)
    prepare_workspace(workspace)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.doc_id, d.filename, d.media_type, d.text, d.status, d.index_error,
                   d.text_issues, d.uploaded_by, d.created_at, d.indexed_at, d.deleted_at,
                   u.email AS uploader_email
            FROM documents d
            JOIN users u ON u.id = d.uploaded_by
            WHERE d.deleted_at IS NULL
            ORDER BY d.created_at DESC, d.rowid DESC
            """
        ).fetchall()
    return [_record(row, user_id, role) for row in rows]


def document_filenames(db_path: Path, doc_ids: list[str]) -> dict[str, str]:
    """Return filenames for the given document ids. Missing ids are omitted."""
    if not doc_ids:
        return {}
    init_documents(db_path)
    placeholders = ", ".join("?" for _ in doc_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT doc_id, filename FROM documents WHERE doc_id IN ({placeholders})",
            doc_ids,
        ).fetchall()
    return {row["doc_id"]: row["filename"] for row in rows}


def delete_document(
    db_path: Path,
    workspace: Path,
    doc_id: str,
    user_id: str,
    role: str,
) -> None:
    """Soft-delete a document and remove its corpus file."""
    init_documents(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None or row["deleted_at"] is not None:
            raise UnknownDocumentError
        if role != "admin" and row["uploaded_by"] != user_id:
            raise DeleteForbiddenError
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE doc_id = ?",
            (_iso(_now()), doc_id),
        )
    (workspace / "corpus" / f"{doc_id}.json").unlink(missing_ok=True)


def _seed_champion(workspace: Path, default_config: Path) -> Path:
    champion = workspace / "configs" / "champion.yaml"
    if champion.exists():
        return champion
    config = RAGConfig.from_yaml(default_config)
    corpus = workspace / "corpus"
    index_dir = workspace / "indexes" / "current"
    seeded = config.model_copy(
        update={
            "retrieval": config.retrieval.model_copy(update={"lancedb_index_dir": str(index_dir)}),
            "data": config.data.model_copy(
                update={
                    "path_or_repo": str(corpus),
                    "source_type": DataSourceType.DIRECTORY,
                }
            ),
        }
    )
    champion.write_text(seeded.to_yaml(), encoding="utf-8")
    return champion


def _filename(filename: str | None) -> tuple[str, str]:
    if filename is None or not filename.strip():
        raise UploadRejected("Choose a file to upload.")
    stored = Path(filename.replace("\\", "/")).name.strip()
    if not stored or stored in {".", ".."}:
        raise UploadRejected("Choose a file to upload.")
    suffix = Path(stored).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise UploadRejected("Upload a text, Markdown, or PDF file.")
    return stored, suffix


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadRejected("This file is not valid UTF-8.") from exc


def _read_pdf(
    content: bytes, reading: str | None
) -> tuple[str, list[TextIssue], DocumentStatus, str | None]:
    if not content.startswith(b"%PDF"):
        raise UploadRejected("This file is not a PDF.")
    if reading is None or not reading.strip():
        raise UploadRejected("Read this PDF before uploading.")
    try:
        parsed = _PdfReading.model_validate_json(reading)
    except ValidationError as exc:
        raise UploadRejected("The PDF reading is not valid.") from exc
    issues = _issues(parsed)
    text = parsed.text
    if text.strip():
        return text, issues, "pending", None
    kinds = {issue.kind for issue in issues}
    if "scanned" in kinds:
        return "", issues, "failed", SCANNED_EMPTY
    if "garbled" in kinds:
        return "", issues, "failed", GARBLED_EMPTY
    return "", issues, "failed", EMPTY_PDF


def _issues(parsed: _PdfReading) -> list[TextIssue]:
    found: set[tuple[int, IssueKind]] = set()
    for page in parsed.pages:
        for reason in page.reasons:
            if reason not in _KNOWN_REASONS:
                raise UploadRejected("The PDF reading is not valid.")
            if reason == "scanned" or reason == "garbled":
                found.add((page.page, reason))
    return [TextIssue(page=page, kind=kind) for page, kind in sorted(found)]


def _write_corpus(workspace: Path, doc_id: str, text: str, filename: str) -> None:
    path = workspace / "corpus" / f"{doc_id}.json"
    payload = {"doc_id": doc_id, "text": text, "filename": filename}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _dump_issues(issues: list[TextIssue]) -> str:
    return json.dumps([{"page": issue.page, "kind": issue.kind} for issue in issues])


def _load_issues(raw: str) -> list[TextIssue]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    issues: list[TextIssue] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        page = item.get("page")
        if kind in _HIGHLIGHT_REASONS and isinstance(page, int):
            issues.append(TextIssue(page=page, kind=kind))
    return issues


def _record(row: sqlite3.Row, user_id: str, role: str) -> DocumentRecord:
    return DocumentRecord(
        doc_id=row["doc_id"],
        filename=row["filename"],
        media_type=row["media_type"],
        text=row["text"],
        status=row["status"],
        index_error=row["index_error"],
        issues=_load_issues(row["text_issues"]),
        uploaded_by=row["uploaded_by"],
        uploader_email=row["uploader_email"],
        created_at=row["created_at"],
        indexed_at=row["indexed_at"],
        deleted_at=row["deleted_at"],
        can_delete=role == "admin" or row["uploaded_by"] == user_id,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()

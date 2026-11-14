"""Workspace uploads, the Library list, and soft delete."""

from collections.abc import Iterator
import json

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.schema import DataLoaderConfig, DataSourceType
from deepfix_rag.config.settings import Settings
from deepfix_rag.data import DataLoader
from deepfix_rag.documents import EMPTY_PDF, GARBLED_EMPTY, SCANNED_EMPTY
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.unit]


@pytest.fixture
def document_settings(tmp_path):
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
    )


@pytest.fixture
def client(document_settings: Settings, identity: IdentityIssuer) -> Iterator[TestClient]:
    embed_model = MockEmbedding(embed_dim=16)
    app = create_app(
        document_settings, embed_model=embed_model, token_verifier=identity.verifier()
    )
    with TestClient(app) as test_client:
        yield test_client


def _reading(text: str, pages: list[dict[str, object]] | None = None) -> dict[str, str]:
    return {"reading": json.dumps({"text": text, "pages": pages or []})}


def test_workspace_layout_and_champion_seed(
    client: TestClient, document_settings: Settings
) -> None:
    client.get("/health")
    workspace = document_settings.workspace_dir
    for name in ("corpus", "indexes", "feedback", "configs", "runs"):
        assert (workspace / name).is_dir()
    assert (workspace / "indexes" / "current").is_dir()
    assert document_settings.product_db_path.is_file()
    assert workspace not in document_settings.product_db_path.parents

    champion = workspace / "configs" / "champion.yaml"
    text = champion.read_text(encoding="utf-8")
    assert "s3://" not in text
    assert "HF:" not in text
    assert "dip-bundestag" not in text
    assert str(workspace / "corpus") in text
    assert str(workspace / "indexes" / "current") in text
    assert "source_type: directory" in text

    champion.write_text("kept: true\n", encoding="utf-8")
    with TestClient(
        create_app(document_settings, embed_model=MockEmbedding(embed_dim=16))
    ) as again:
        again.get("/health")
    assert champion.read_text(encoding="utf-8") == "kept: true\n"


def test_text_upload_list_order_and_corpus(
    client: TestClient, document_settings: Settings, identity: IdentityIssuer
) -> None:
    assert client.get("/documents").status_code == 401
    identity.sign_in(client, "ada@example.com")

    first = client.post("/documents", files={"file": ("notes.txt", b"Harbor notes", "text/plain")})
    assert first.status_code == 201
    body = first.json()
    assert body["filename"] == "notes.txt"
    assert body["media_type"] == "text/plain"
    assert body["status"] == "pending"
    assert body["uploader_email"] == "ada@example.com"
    assert body["can_delete"] is True
    assert body["issues"] == []
    assert body["doc_id"].startswith("ws_")
    assert "::" not in body["doc_id"]
    assert DataLoader.parent_doc_id(body["doc_id"]) == body["doc_id"]

    corpus_path = document_settings.workspace_dir / "corpus" / f"{body['doc_id']}.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert corpus == {"doc_id": body["doc_id"], "text": "Harbor notes", "filename": "notes.txt"}

    second = client.post(
        "/documents",
        files={"file": ("readme.md", b"# Hello", "text/markdown")},
    )
    assert second.status_code == 201
    assert second.json()["media_type"] == "text/markdown"

    listed = client.get("/documents")
    assert listed.status_code == 200
    names = [item["filename"] for item in listed.json()]
    assert names == ["readme.md", "notes.txt"]
    assert "text" not in listed.json()[0]

    loaded = DataLoader(
        DataLoaderConfig(
            path_or_repo=str(document_settings.workspace_dir / "corpus"),
            source_type=DataSourceType.DIRECTORY,
        )
    ).load_corpus()
    by_id = {doc.id_: doc for doc in loaded}
    assert by_id[body["doc_id"]].text == "Harbor notes"
    assert by_id[body["doc_id"]].metadata["filename"] == "notes.txt"


def test_upload_rejections(client: TestClient, identity: IdentityIssuer) -> None:
    identity.sign_in(client, "ada@example.com")

    kind = client.post("/documents", files={"file": ("scan.tiff", b"not", "image/tiff")})
    assert kind.status_code == 400
    assert kind.json()["detail"] == "Upload a text, Markdown, or PDF file."

    binary = client.post("/documents", files={"file": ("notes.txt", b"\xff", "text/plain")})
    assert binary.status_code == 400
    assert binary.json()["detail"] == "This file is not valid UTF-8."

    huge = b"a" * (20 * 1024 * 1024 + 1)
    oversized = client.post("/documents", files={"file": ("notes.txt", huge, "text/plain")})
    assert oversized.status_code == 400
    assert oversized.json()["detail"] == "Files must be 20 MB or smaller."

    missing = client.post(
        "/documents",
        files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "Read this PDF before uploading."

    fake = client.post(
        "/documents",
        files={"file": ("scan.pdf", b"not a pdf", "application/pdf")},
        data=_reading("hello"),
    )
    assert fake.status_code == 400
    assert fake.json()["detail"] == "This file is not a PDF."

    invalid = client.post(
        "/documents",
        files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
        data={"reading": "[]"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "The PDF reading is not valid."


def test_pdf_highlights_and_empty_text(
    client: TestClient, document_settings: Settings, identity: IdentityIssuer
) -> None:
    identity.sign_in(client, "ada@example.com")
    pdf = b"%PDF-1.4\n"

    mixed = client.post(
        "/documents",
        files={"file": ("mixed.PDF", pdf, "application/pdf")},
        data=_reading(
            "The committee approved the annual harbor budget.",
            [{"page": 2, "reasons": ["scanned", "no-text"]}, {"page": 3, "reasons": ["garbled"]}],
        ),
    )
    assert mixed.status_code == 201
    body = mixed.json()
    assert body["status"] == "pending"
    assert body["filename"] == "mixed.PDF"
    assert body["issues"] == [
        {"page": 2, "kind": "scanned"},
        {"page": 3, "kind": "garbled"},
    ]
    assert body["index_error"] is None

    scanned = client.post(
        "/documents",
        files={"file": ("scan.pdf", pdf, "application/pdf")},
        data=_reading("  ", [{"page": 1, "reasons": ["scanned"]}]),
    )
    assert scanned.status_code == 201
    assert scanned.json()["status"] == "failed"
    assert scanned.json()["index_error"] == SCANNED_EMPTY
    scanned_id = scanned.json()["doc_id"]
    assert not (document_settings.workspace_dir / "corpus" / f"{scanned_id}.json").exists()

    garbled = client.post(
        "/documents",
        files={"file": ("garbled.pdf", pdf, "application/pdf")},
        data=_reading("", [{"page": 1, "reasons": ["garbled"]}]),
    )
    assert garbled.json()["status"] == "failed"
    assert garbled.json()["index_error"] == GARBLED_EMPTY

    blank = client.post(
        "/documents",
        files={"file": ("blank.pdf", pdf, "application/pdf")},
        data=_reading("", [{"page": 1, "reasons": ["no-text"]}]),
    )
    assert blank.json()["status"] == "failed"
    assert blank.json()["index_error"] == EMPTY_PDF
    assert blank.json()["issues"] == []


def test_delete_permissions(
    client: TestClient, document_settings: Settings, identity: IdentityIssuer
) -> None:
    identity.sign_in(client, "ada@example.com")
    created = client.post("/documents", files={"file": ("notes.txt", b"Keep", "text/plain")})
    doc_id = created.json()["doc_id"]

    identity.sign_in(client, "bea@example.com")
    forbidden = client.delete(f"/documents/{doc_id}")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "You can only delete files you uploaded."
    assert client.get("/documents").json()[0]["can_delete"] is False

    identity.sign_in(client, "ada@example.com")
    assert client.delete(f"/documents/{doc_id}").status_code == 204
    client.app.state.index.wait()
    assert client.get("/documents").json() == []
    assert not (document_settings.workspace_dir / "corpus" / f"{doc_id}.json").exists()
    with connect(document_settings.product_db_path) as conn:
        row = conn.execute(
            "SELECT filename, deleted_at FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
    assert row["filename"] == "notes.txt"
    assert row["deleted_at"] is not None
    missing = client.delete(f"/documents/{doc_id}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "No document with that id."

    other = client.post("/documents", files={"file": ("other.txt", b"Other", "text/plain")})
    other_id = other.json()["doc_id"]
    identity.sign_in(client, "admin@example.com", "admin")
    assert client.delete(f"/documents/{other_id}").status_code == 204
    client.app.state.index.wait()
    assert client.get("/documents").json() == []

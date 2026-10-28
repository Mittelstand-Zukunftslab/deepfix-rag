"""Per-document inserts and deletes leave every other document's chunks untouched."""

from collections.abc import Iterator

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]


class SelectiveEmbedding(MockEmbedding):
    """Fail embeddings whose text is marked unindexable. Queries still embed."""

    def _get_text_embedding(self, text: str) -> list[float]:
        if "UNINDEXABLE" in text:
            raise RuntimeError("embed failed")
        return list(self._get_vector())


@pytest.fixture
def index_settings(tmp_path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
    )


@pytest.fixture
def client(index_settings: Settings, identity: IdentityIssuer) -> Iterator[TestClient]:
    embed_model = MockEmbedding(embed_dim=16)
    with TestClient(
        create_app(index_settings, embed_model=embed_model, token_verifier=identity.verifier())
    ) as test_client:
        yield test_client


def _upload(client: TestClient, filename: str, text: str) -> str:
    created = client.post("/documents", files={"file": (filename, text.encode(), "text/plain")})
    assert created.status_code == 201, created.text
    client.app.state.index.wait()
    return created.json()["doc_id"]


def _vector(value: object) -> tuple[object, ...]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (bytes, bytearray)):
        return tuple(value)
    return tuple(value)


def _chunks(client: TestClient) -> dict[str, list[tuple[str, tuple[object, ...]]]]:
    lease = client.app.state.index.checkout()
    assert lease is not None
    try:
        rows = lease.service.chunk_rows()
    finally:
        client.app.state.index.release(lease)
    grouped: dict[str, list[tuple[str, tuple[object, ...]]]] = {}
    for row in rows:
        doc_id = str(row["doc_id"])
        grouped.setdefault(doc_id, []).append((str(row["id"]), _vector(row["vector"])))
    return {doc_id: sorted(chunks) for doc_id, chunks in grouped.items()}


def _hits(client: TestClient, query: str) -> list[dict[str, object]]:
    found = client.post("/retrieval", json={"query": query})
    assert found.status_code == 200, found.text
    return found.json()["hits"]


def test_upload_and_delete_touch_only_that_documents_chunks(
    client: TestClient, index_settings: Settings, identity: IdentityIssuer
) -> None:
    identity.sign_in(client, "ada@example.com")
    doc_a = _upload(client, "a.txt", "Alpha harbor budget was approved.")
    doc_b = _upload(client, "b.txt", "Beta lighthouse schedule remains.")
    before = _chunks(client)
    assert set(before) == {doc_a, doc_b}
    assert before[doc_a]
    assert before[doc_b]

    client.app.state.index.on_document_added(doc_a)
    client.app.state.index.wait()
    retried = _chunks(client)
    assert len(retried[doc_a]) == len(before[doc_a])
    assert retried[doc_b] == before[doc_b]
    assert sum(len(chunks) for chunks in retried.values()) == sum(
        len(chunks) for chunks in before.values()
    )

    doc_c = _upload(client, "c.txt", "Gamma zephyrquartz cargo manifest.")
    listed = {row["doc_id"]: row["status"] for row in client.get("/documents").json()}
    assert listed == {doc_a: "indexed", doc_b: "indexed", doc_c: "indexed"}
    with_c = _chunks(client)
    assert with_c[doc_a] == retried[doc_a]
    assert with_c[doc_b] == retried[doc_b]
    assert with_c[doc_c]
    assert any(
        hit["doc_id"] == doc_c and "zephyrquartz" in str(hit["text"])
        for hit in _hits(client, "zephyrquartz")
    )

    deleted = client.delete(f"/documents/{doc_b}")
    assert deleted.status_code == 204
    client.app.state.index.wait()
    remaining = _chunks(client)
    assert doc_b not in remaining
    assert remaining[doc_a] == with_c[doc_a]
    assert remaining[doc_c] == with_c[doc_c]
    assert all(hit["doc_id"] != doc_b for hit in _hits(client, "lighthouse"))
    assert not (index_settings.workspace_dir / "corpus" / f"{doc_b}.json").exists()
    assert (index_settings.workspace_dir / "corpus" / f"{doc_a}.json").exists()
    with connect(index_settings.product_db_path) as conn:
        row = conn.execute(
            "SELECT deleted_at FROM documents WHERE doc_id = ?",
            (doc_b,),
        ).fetchone()
    assert row["deleted_at"] is not None
    assert client.get("/documents").json()[0]["doc_id"] != doc_b


def test_failed_insert_leaves_indexed_documents_searchable(
    client: TestClient, identity: IdentityIssuer
) -> None:
    identity.sign_in(client, "ada@example.com")
    doc_a = _upload(client, "a.txt", "Alpha harbor budget was approved.")
    before = _chunks(client)
    lease = client.app.state.index.checkout()
    assert lease is not None
    lease.service._index._embed_model = SelectiveEmbedding(embed_dim=16)
    client.app.state.index.release(lease)

    created = client.post(
        "/documents",
        files={"file": ("c.txt", b"UNINDEXABLE gamma note.", "text/plain")},
    )
    assert created.status_code == 201
    doc_c = created.json()["doc_id"]
    client.app.state.index.wait()

    listed = {row["doc_id"]: row for row in client.get("/documents").json()}
    assert listed[doc_a]["status"] == "indexed"
    assert listed[doc_c]["status"] == "failed"
    assert "embed failed" in listed[doc_c]["index_error"]
    assert _chunks(client) == before
    assert any(hit["doc_id"] == doc_a for hit in _hits(client, "harbor"))
    assert client.get("/index").json()["serving_build_id"]


def test_startup_resumes_indexing_and_deleting(
    index_settings: Settings, identity: IdentityIssuer
) -> None:
    embed_model = MockEmbedding(embed_dim=16)
    with TestClient(
        create_app(index_settings, embed_model=embed_model, token_verifier=identity.verifier())
    ) as client:
        identity.sign_in(client, "ada@example.com")
        doc_a = _upload(client, "a.txt", "Alpha harbor budget was approved.")
        doc_b = _upload(client, "b.txt", "Beta lighthouse schedule remains.")
        build_id = client.get("/index").json()["serving_build_id"]
        kept = _chunks(client)[doc_a]
        with connect(index_settings.product_db_path) as conn:
            conn.execute(
                "UPDATE documents SET status = 'indexing' WHERE doc_id = ?",
                (doc_a,),
            )
            conn.execute(
                "UPDATE documents SET status = 'deleting' WHERE doc_id = ?",
                (doc_b,),
            )

    with TestClient(
        create_app(index_settings, embed_model=embed_model, token_verifier=identity.verifier())
    ) as restarted:
        restarted.app.state.index.wait()
        status = restarted.get("/index")
        assert status.status_code == 401
        identity.sign_in(restarted, "ada@example.com")
        restarted.app.state.index.wait()
        snapshot = restarted.get("/index").json()
        assert snapshot["serving_build_id"] == build_id
        assert snapshot["status"] == "ready"
        listed = {row["doc_id"]: row["status"] for row in restarted.get("/documents").json()}
        assert listed == {doc_a: "indexed"}
        chunks = _chunks(restarted)
        assert doc_b not in chunks
        assert chunks[doc_a]
        assert kept
        assert not (index_settings.workspace_dir / "corpus" / f"{doc_b}.json").exists()

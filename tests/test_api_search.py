"""Search stores the passages that were shown and does not require a trace."""

import hashlib

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.interactions import get_interaction, passage_key

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def search_settings(tmp_path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        ALLOWED_EMAIL_DOMAINS="example.com",
        WEB_ORIGIN="http://localhost:8080",
        MAX_DISPLAYED_HITS=10,
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


@pytest.fixture
def client(search_settings: Settings):
    mlflow.set_tracking_uri(search_settings.MLFLOW_TRACKING_URI)
    embed_model = MockEmbedding(embed_dim=16)
    with TestClient(create_app(search_settings, embed_model=embed_model)) as test_client:
        yield test_client


def _login(client: TestClient) -> None:
    created = client.post(
        "/auth/register",
        json={"email": "ada@example.com", "password": "correct-horse"},
    )
    assert created.status_code == 201
    signed_in = client.post(
        "/auth/login",
        json={"email": "ada@example.com", "password": "correct-horse"},
    )
    assert signed_in.status_code == 200


def _index_note(client: TestClient, text: str, filename: str) -> None:
    uploaded = client.post(
        "/documents",
        files={"file": (filename, text.encode(), "text/plain")},
    )
    assert uploaded.status_code == 201
    client.app.state.index.wait()


def test_empty_query_and_missing_index_are_rejected(client: TestClient) -> None:
    _login(client)
    empty = client.post("/retrieval", json={"query": "   "})
    assert empty.status_code == 400
    missing = client.post("/retrieval", json={"query": "harbor"})
    assert missing.status_code == 503
    assert missing.json()["detail"] == "The index is being prepared. Try again in a minute."
    client.cookies.clear()
    assert client.post("/retrieval", json={"query": "harbor"}).status_code == 401


def test_search_snapshots_hits_and_keeps_working_when_tracing_fails(
    client: TestClient, search_settings: Settings
) -> None:
    _login(client)
    _index_note(client, "The harbor budget was approved.", "harbor.txt")
    mlflow.set_tracking_uri("http://127.0.0.1:9")
    found = client.post("/retrieval", json={"query": "harbor budget"})
    assert found.status_code == 200
    body = found.json()
    assert body["hits"]
    hit = body["hits"][0]
    assert hit["rank"] == 1
    assert hit["document_filename"] == "harbor.txt"
    assert "harbor budget" in hit["text"].lower() or "harbor" in hit["text"].lower()
    assert hit["vote"] is None
    stored = get_interaction(search_settings.product_db_path, body["interaction_id"])
    assert stored is not None
    assert stored.kind == "retrieval"
    assert stored.trace_id is None
    assert stored.hits[0].passage_key == passage_key(stored.hits[0].text)
    assert stored.hits[0].passage_key == hashlib.sha256(stored.hits[0].text.encode()).hexdigest()
    assert stored.build_id
    assert stored.config_path.endswith("champion.yaml")


def test_search_stores_the_trace_id(client: TestClient, search_settings: Settings) -> None:
    _login(client)
    _index_note(client, "The harbor budget was approved.", "harbor.txt")
    mlflow.set_tracking_uri(search_settings.MLFLOW_TRACKING_URI)
    found = client.post("/retrieval", json={"query": "harbor budget"})
    assert found.status_code == 200
    stored = get_interaction(search_settings.product_db_path, found.json()["interaction_id"])
    assert stored is not None
    assert stored.trace_id
    trace = mlflow.get_trace(stored.trace_id, flush=True)
    assert trace is not None
    tags = trace.info.tags or {}
    assert tags.get("interaction_id") == stored.interaction_id

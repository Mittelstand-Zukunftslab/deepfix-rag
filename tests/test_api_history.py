"""Admins review every user's searches and asks with the answers and feedback."""

from pathlib import Path

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import record_vote
from deepfix_rag.interactions import record_interaction
from deepfix_rag.pipeline import RetrievalResult
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def _record(
    settings: Settings,
    identity: IdentityIssuer,
    email: str,
    query: str,
    *,
    kind: str = "ask",
    answer: str | None = "An answer.",
    error: str | None = None,
) -> str:
    return record_interaction(
        settings.product_db_path,
        kind=kind,
        user_id=identity.user_id(email),
        user_email=email,
        query=query,
        build_id="build-1",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-a", score=0.9, text="first passage"),
            RetrievalResult(doc_id="doc-b", score=0.5, text="second passage"),
        ],
        answer=answer if kind == "ask" else None,
        error=error,
    ).interaction_id


def _vote(
    settings: Settings,
    identity: IdentityIssuer,
    email: str,
    interaction_id: str,
    target: str,
    action: str,
    rank: int | None = None,
) -> None:
    record_vote(
        settings.product_db_path,
        user_id=identity.user_id(email),
        interaction_id=interaction_id,
        target=target,
        action=action,
        rank=rank,
    )


def test_only_admins_can_review_history(settings: Settings, identity: IdentityIssuer) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        assert client.get("/admin/interactions").status_code == 401
        identity.sign_in(client, "ada@example.com")
        denied = client.get("/admin/interactions")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Only admins can review history."
        assert client.get("/admin/interactions/anything").status_code == 403


def test_history_lists_every_user_with_answers_and_feedback(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        liked = _record(settings, identity, "ada@example.com", "harbor budget")
        _vote(settings, identity, "ada@example.com", liked, "answer", "up")
        _vote(settings, identity, "ada@example.com", liked, "passage", "up", rank=1)
        disliked = _record(settings, identity, "bea@example.com", "lighthouse repairs")
        _vote(settings, identity, "bea@example.com", disliked, "answer", "up")
        _vote(settings, identity, "bea@example.com", disliked, "answer", "down")
        _vote(settings, identity, "bea@example.com", disliked, "passage", "down", rank=2)
        cleared = _record(
            settings, identity, "bea@example.com", "ferry timetable", kind="retrieval"
        )
        _vote(settings, identity, "bea@example.com", cleared, "passage", "up", rank=1)
        _vote(settings, identity, "bea@example.com", cleared, "passage", "clear", rank=1)
        failed = _record(
            settings,
            identity,
            "ada@example.com",
            "harbor 100% plan",
            answer=None,
            error="The answer could not be generated.",
        )

        identity.sign_in(client, "admin@example.com", "admin")
        listed = client.get("/admin/interactions")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == 4
        assert [item["interaction_id"] for item in body["items"]] == [
            failed,
            cleared,
            disliked,
            liked,
        ]
        by_id = {item["interaction_id"]: item for item in body["items"]}
        assert by_id[liked]["user_email"] == "ada@example.com"
        assert by_id[liked]["answer"] == "An answer."
        assert by_id[liked]["hit_count"] == 2
        assert by_id[liked]["answer_votes"] == {"up": 1, "down": 0}
        assert by_id[liked]["passage_votes"] == {"up": 1, "down": 0}
        assert by_id[disliked]["answer_votes"] == {"up": 0, "down": 1}
        assert by_id[disliked]["passage_votes"] == {"up": 0, "down": 1}
        assert by_id[cleared]["kind"] == "retrieval"
        assert by_id[cleared]["answer"] is None
        assert by_id[cleared]["passage_votes"] == {"up": 0, "down": 0}
        assert by_id[failed]["error"] == "The answer could not be generated."

        def ids(**params: str | int) -> list[str]:
            response = client.get("/admin/interactions", params=params)
            assert response.status_code == 200, response.text
            return [item["interaction_id"] for item in response.json()["items"]]

        assert ids(kind="retrieval") == [cleared]
        assert ids(feedback="any") == [disliked, liked]
        assert ids(feedback="negative") == [disliked]
        assert ids(feedback="none") == [failed, cleared]
        assert ids(q="harbor") == [failed, liked]
        assert ids(q="BEA@") == [cleared, disliked]
        assert ids(q="100%") == [failed]
        assert ids(q="100_") == []
        paged = client.get("/admin/interactions", params={"limit": 2, "offset": 2}).json()
        assert paged["total"] == 4
        assert [item["interaction_id"] for item in paged["items"]] == [disliked, liked]
        assert client.get("/admin/interactions", params={"limit": 0}).status_code == 400
        assert client.get("/admin/interactions", params={"feedback": "x"}).status_code == 400


def test_review_shows_passages_and_their_votes(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        disliked = _record(settings, identity, "bea@example.com", "lighthouse repairs")
        _vote(settings, identity, "bea@example.com", disliked, "answer", "down")
        _vote(settings, identity, "bea@example.com", disliked, "passage", "down", rank=2)

        identity.sign_in(client, "admin@example.com", "admin")
        review = client.get(f"/admin/interactions/{disliked}")
        assert review.status_code == 200, review.text
        body = review.json()
        assert body["entry"]["query"] == "lighthouse repairs"
        assert body["entry"]["answer_votes"] == {"up": 0, "down": 1}
        assert body["build_id"] == "build-1"
        assert [
            (hit["rank"], hit["document_filename"], hit["text"], hit["votes"])
            for hit in body["hits"]
        ] == [
            (1, "doc-a", "first passage", {"up": 0, "down": 0}),
            (2, "doc-b", "second passage", {"up": 0, "down": 1}),
        ]
        missing = client.get("/admin/interactions/unknown")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "No interaction with that id."


def test_searches_record_the_email_of_the_user(
    settings: Settings, identity: IdentityIssuer
) -> None:
    app = create_app(
        settings, embed_model=MockEmbedding(embed_dim=16), token_verifier=identity.verifier()
    )
    with TestClient(app) as client:
        identity.sign_in(client, "ada@example.com")
        uploaded = client.post(
            "/documents",
            files={"file": ("harbor.txt", b"The harbor budget was approved.", "text/plain")},
        )
        assert uploaded.status_code == 201
        client.app.state.index.wait()
        found = client.post("/retrieval", json={"query": "harbor"})
        assert found.status_code == 200, found.text

        identity.sign_in(client, "admin@example.com", "admin")
        items = client.get("/admin/interactions").json()["items"]
        assert [(item["user_email"], item["query"]) for item in items] == [
            ("ada@example.com", "harbor")
        ]
        hits = client.get(f"/admin/interactions/{items[0]['interaction_id']}").json()["hits"]
        assert hits[0]["document_filename"] == "harbor.txt"

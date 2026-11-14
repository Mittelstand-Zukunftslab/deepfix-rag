"""Optional comments on a vote: stored append-only, read by admins, mirrored as the rationale."""

from pathlib import Path

from fastapi.testclient import TestClient
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import build_questions, sync_pending
from deepfix_rag.interactions import record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect
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


def _trace_id() -> str:
    mlflow.set_experiment("deepfix_product")
    with mlflow.start_span(name="ask") as span:
        trace_id = str(span.trace_id)
    mlflow.flush_trace_async_logging()
    return trace_id


def _valid(trace_id: str) -> list[tuple[object, str | None, object]]:
    """Value, rationale, and reason of each assessment that is still valid on the trace."""
    return [
        (a.value, a.rationale, (a.metadata or {}).get("reason"))
        for a in mlflow.get_trace(trace_id, flush=True).info.assessments
        if a.valid
    ]


def _record(
    settings: Settings, identity: IdentityIssuer, email: str, *, trace_id: str | None = None
) -> str:
    return record_interaction(
        settings.product_db_path,
        kind="ask",
        user_id=identity.user_id(email),
        user_email=email,
        query="Harbor budget",
        build_id="build-1",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-a", score=0.9, text="first passage"),
            RetrievalResult(doc_id="doc-b", score=0.5, text="second passage"),
        ],
        answer="The budget was approved.",
        trace_id=trace_id,
    ).interaction_id


def _vote(client: TestClient, interaction_id: str, target: str, action: str, rank=None) -> None:
    response = client.post(
        "/feedback",
        json={"interaction_id": interaction_id, "target": target, "rank": rank, "action": action},
    )
    assert response.status_code == 200, response.text


def _comment(client: TestClient, interaction_id: str, target: str, rank=None, **body):
    return client.post(
        "/feedback/comments",
        json={"interaction_id": interaction_id, "target": target, "rank": rank, **body},
    )


def test_comments_need_a_vote_and_a_fitting_reason(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        client.app.state.feedback.stop()
        interaction_id = _record(settings, identity, "ada@example.com")
        assert _comment(client, interaction_id, "answer", text="x").status_code == 401

        identity.sign_in(client, "ada@example.com")
        no_vote = _comment(client, interaction_id, "answer", text="Too short.")
        assert no_vote.status_code == 400
        assert no_vote.json()["detail"] == "Vote before adding a comment."

        _vote(client, interaction_id, "answer", "up")
        praise_reason = _comment(client, interaction_id, "answer", reason="wrong")
        assert praise_reason.status_code == 400
        assert praise_reason.json()["detail"] == "That reason does not fit this vote."
        praised = _comment(client, interaction_id, "answer", text="  Clear and cited.  ")
        assert praised.status_code == 201, praised.text
        assert set(praised.json()) == {"comment_id", "created_at"}

        _vote(client, interaction_id, "passage", "down", rank=2)
        wrong_target = _comment(client, interaction_id, "passage", rank=2, reason="incomplete")
        assert wrong_target.status_code == 400
        empty = _comment(client, interaction_id, "passage", rank=2, text="   ")
        assert empty.status_code == 400
        assert empty.json()["detail"] == "Choose a reason or write a comment."
        too_long = _comment(client, interaction_id, "passage", rank=2, text="x" * 1001)
        assert too_long.status_code == 400
        assert _comment(client, interaction_id, "passage", rank=9, text="x").status_code == 400
        assert (
            _comment(client, interaction_id, "passage", rank=2, reason="nope").status_code == 400
        )
        first = _comment(client, interaction_id, "passage", rank=2, reason="outdated")
        second = _comment(
            client, interaction_id, "passage", rank=2, reason="other", text="Wrong year."
        )
        assert first.status_code == 201 and second.status_code == 201

        identity.sign_in(client, "bea@example.com")
        assert _comment(client, interaction_id, "answer", text="x").status_code == 404

        identity.sign_in(client, "admin@example.com", "admin")
        listed = client.get("/admin/interactions").json()["items"]
        assert listed[0]["comment_count"] == 3
        assert (
            client.get("/admin/interactions", params={"feedback": "commented"}).json()["total"]
            == 1
        )
        review = client.get(f"/admin/interactions/{interaction_id}").json()
        assert [(c["vote"], c["reason"], c["text"]) for c in review["answer_comments"]] == [
            ("up", None, "Clear and cited.")
        ]
        assert [(c["vote"], c["reason"], c["text"]) for c in review["hits"][1]["comments"]] == [
            ("down", "outdated", ""),
            ("down", "other", "Wrong year."),
        ]
        assert review["hits"][0]["comments"] == []


def test_comments_never_change_the_eval_labels(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        client.app.state.feedback.stop()
        interaction_id = _record(settings, identity, "ada@example.com")
        identity.sign_in(client, "ada@example.com")
        _vote(client, interaction_id, "passage", "up", rank=1)
        before = build_questions(settings.product_db_path)
        assert (
            _comment(client, interaction_id, "passage", rank=1, text="Ignore doc-b").status_code
            == 201
        )
        assert build_questions(settings.product_db_path) == before


def test_the_latest_comment_reaches_mlflow_as_the_rationale(
    settings: Settings, identity: IdentityIssuer
) -> None:
    with TestClient(create_app(settings, token_verifier=identity.verifier())) as client:
        client.app.state.feedback.stop()
        trace_id = _trace_id()
        interaction_id = _record(settings, identity, "ada@example.com", trace_id=trace_id)
        identity.sign_in(client, "ada@example.com")
        _vote(client, interaction_id, "answer", "down")
        assert sync_pending(settings.product_db_path) == 1

        assert (
            _comment(
                client, interaction_id, "answer", reason="incomplete", text="Misses 2025."
            ).status_code
            == 201
        )
        assert sync_pending(settings.product_db_path) == 1
        assert _valid(trace_id) == [(False, "Misses 2025.", "incomplete")]

        _vote(client, interaction_id, "answer", "up")
        assert sync_pending(settings.product_db_path) == 1
        assert _valid(trace_id) == [(True, None, None)]

        _vote(client, interaction_id, "answer", "down")
        assert _comment(client, interaction_id, "answer", reason="wrong").status_code == 201
        assert sync_pending(settings.product_db_path) == 1
        assert sync_pending(settings.product_db_path) == 0
        assert _valid(trace_id) == [(False, None, "wrong")]

        with connect(settings.product_db_path) as conn:
            statuses = {
                row["sync_status"]
                for row in conn.execute("SELECT sync_status FROM feedback_events")
            }
        assert statuses == {"synced"}

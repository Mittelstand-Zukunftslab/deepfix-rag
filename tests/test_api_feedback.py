"""Votes commit locally first, then the outbox mirrors them onto the trace."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import sync_pending
from deepfix_rag.interactions import get_interaction, record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    app = create_app(
        settings,
        embed_model=MockEmbedding(embed_dim=16),
        llm=MockLLM(),
        token_verifier=identity.verifier(),
    )
    return TestClient(app)


def _trace_id() -> str:
    mlflow.set_experiment("deepfix_product")
    with mlflow.start_span(name="retrieval") as span:
        trace_id = str(span.trace_id)
    mlflow.flush_trace_async_logging()
    return trace_id


def test_votes_stick_after_clear_and_switch(settings: Settings, identity: IdentityIssuer) -> None:
    with _client(settings, identity) as client:
        identity.sign_in(client, "ada@example.com")
        client.app.state.feedback.stop()
        uploaded = client.post(
            "/documents",
            files={"file": ("harbor.txt", b"The harbor budget was approved.", "text/plain")},
        )
        assert uploaded.status_code == 201
        client.app.state.index.wait()
        found = client.post("/retrieval", json={"query": "harbor"})
        assert found.status_code == 200, found.text
        interaction_id = found.json()["interaction_id"]
        up = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "up",
            },
        )
        assert up.status_code == 200, up.text
        assert up.json()["vote"] == "up"
        with connect(settings.product_db_path) as conn:
            pending = conn.execute(
                "SELECT sync_status FROM feedback_events WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        assert pending["sync_status"] == "pending"
        stored = client.get(f"/interactions/{interaction_id}")
        assert stored.status_code == 200
        assert stored.json()["hits"][0]["vote"] == "up"
        switched = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "down",
            },
        )
        assert switched.json()["vote"] == "down"
        cleared = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "clear",
            },
        )
        assert cleared.json()["vote"] is None
        with connect(settings.product_db_path) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()["n"]
        assert count == 3
        identity.sign_in(client, "bea@example.com")
        foreign = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "up",
            },
        )
        assert foreign.status_code == 404
        denied = client.get("/admin/feedback/summary")
        assert denied.status_code == 403
        identity.sign_in(client, "admin@example.com", "admin")
        summary = client.get("/admin/feedback/summary")
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["passage_up"] == 0
        assert body["passage_down"] == 0
        assert "dev_question_count" in body
        assert "unsynced_event_count" in body
        assert "no_trace_event_count" in body


def test_outbox_updates_and_stops_when_the_trace_is_missing(settings: Settings) -> None:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    user_id = "ada"
    traced = record_interaction(
        settings.product_db_path,
        kind="ask",
        user_id=user_id,
        query="harbor budget",
        build_id="build",
        config_path="champion.yaml",
        results=[RetrievalResult(doc_id="doc-a", score=0.4, text="harbor text")],
        answer="The harbor budget was approved.",
        trace_id=_trace_id(),
    )
    from deepfix_rag.feedback import record_vote

    assert (
        record_vote(
            settings.product_db_path,
            user_id=user_id,
            interaction_id=traced.interaction_id,
            target="passage",
            rank=1,
            action="up",
        )
        == "up"
    )
    assert sync_pending(settings.product_db_path) == 1
    with connect(settings.product_db_path) as conn:
        mirror = conn.execute("SELECT assessment_id FROM feedback_mirrors").fetchone()
    assert mirror["assessment_id"]
    assessment_id = mirror["assessment_id"]
    trace = mlflow.get_trace(traced.trace_id, flush=True)
    assert trace is not None
    record_vote(
        settings.product_db_path,
        user_id=user_id,
        interaction_id=traced.interaction_id,
        target="passage",
        rank=1,
        action="down",
    )
    assert sync_pending(settings.product_db_path) == 1
    with connect(settings.product_db_path) as conn:
        updated = conn.execute("SELECT assessment_id FROM feedback_mirrors").fetchone()
    assert updated["assessment_id"] != assessment_id
    record_vote(
        settings.product_db_path,
        user_id=user_id,
        interaction_id=traced.interaction_id,
        target="passage",
        rank=1,
        action="up",
    )
    assert sync_pending(settings.product_db_path) == 1
    valid = [
        assessment
        for assessment in mlflow.get_trace(traced.trace_id, flush=True).info.assessments
        if assessment.valid
    ]
    assert [(assessment.name, assessment.value) for assessment in valid] == [
        ("passage_relevance", True)
    ]
    record_vote(
        settings.product_db_path,
        user_id=user_id,
        interaction_id=traced.interaction_id,
        target="answer",
        action="up",
    )
    record_vote(
        settings.product_db_path,
        user_id=user_id,
        interaction_id=traced.interaction_id,
        target="passage",
        rank=1,
        action="clear",
    )
    assert sync_pending(settings.product_db_path) == 2
    with connect(settings.product_db_path) as conn:
        cleared = conn.execute(
            """
            SELECT assessment_id FROM feedback_mirrors
            WHERE target = 'passage'
            """
        ).fetchone()
    assert cleared["assessment_id"] is None

    missing = record_interaction(
        settings.product_db_path,
        kind="retrieval",
        user_id=user_id,
        query="missing trace",
        build_id="build",
        config_path="champion.yaml",
        results=[RetrievalResult(doc_id="doc-a", score=0.1, text="harbor text")],
        trace_id=None,
    )
    record_vote(
        settings.product_db_path,
        user_id=user_id,
        interaction_id=missing.interaction_id,
        target="passage",
        rank=1,
        action="up",
    )
    assert sync_pending(settings.product_db_path) == 1
    assert sync_pending(settings.product_db_path) == 0
    with connect(settings.product_db_path) as conn:
        status = conn.execute(
            "SELECT sync_status FROM feedback_events WHERE interaction_id = ?",
            (missing.interaction_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE feedback_events
            SET created_at = ?, sync_status = 'pending'
            WHERE interaction_id = ?
            """,
            (
                (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
                traced.interaction_id,
            ),
        )
    assert status["sync_status"] == "no_trace"
    from deepfix_rag.feedback import feedback_summary

    summary = feedback_summary(settings.product_db_path)
    assert summary.no_trace_event_count >= 1
    assert summary.unsynced_event_count >= 1
    assert get_interaction(settings.product_db_path, missing.interaction_id) is not None

"""A member's Ask conversations are server objects, private to their owner.

One end-to-end pass over the real app and a real SQLite file: create, list
ordering, rename, get, and delete a thread; every route 404 for another member's
id and 401 unsigned; a delete that keeps the interactions and the votes behind
it, still returned by ``current_votes`` and still exported by ``build_questions``;
a restart between two requests that settles a ``running`` turn ``failed``; a
thread from 2021 that still lists and still loads; the 50-item display cap; and
the query plan of the two reads a thread load makes.

A second test covers the schema itself — idempotence, the ``status`` CHECK, and
the unchanged ``kind`` CHECK on ``interactions`` — because those are properties
of the database rather than of a request.

No mocks. The app is built through ``create_app`` against ``tmp_path``, and the
votes, the MLflow mirror, and the feedback export are the real ones: the point
of D-9 is that they keep working after the conversation is gone.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
import mlflow
import pytest

from deepfix_rag import ask_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.feedback import build_questions, current_votes, sync_pending
from deepfix_rag.interactions import record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]

ADA = "ada@example.com"
BEA = "bea@example.com"
ADMIN = "admin@example.com"
OLD_THREAD_AT = "2021-01-01T00:00:00+00:00"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
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
    """A real trace in the product experiment, so a vote can be mirrored onto it."""
    mlflow.set_experiment("deepfix_product")
    with mlflow.start_span(name="ask") as span:
        trace_id = str(span.trace_id)
    mlflow.flush_trace_async_logging()
    return trace_id


def _turn(
    settings: Settings,
    identity: IdentityIssuer,
    thread_id: str,
    *,
    question: str,
) -> tuple[str, str]:
    """Store one finished turn and the ``interactions`` row that holds its answer."""
    owner_id = identity.user_id(ADA)
    interaction = record_interaction(
        settings.product_db_path,
        kind="ask",
        user_id=owner_id,
        user_email=ADA,
        query=question,
        build_id="build-1",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id="doc-a", score=0.9, text="the harbor budget"),
            RetrievalResult(doc_id="doc-b", score=0.4, text="the lighthouse grant"),
        ],
        answer="The harbor budget was approved.",
        trace_id=_trace_id(),
    )
    turn = ask_chat.append_turn(
        settings.product_db_path,
        thread_id,
        owner_id,
        question=question,
        grounding="grounded",
        citations=[ask_chat.Citation(marker=1, rank=1, doc_id="doc-a")],
    )
    ask_chat.link_interaction(
        settings.product_db_path, turn.turn_id, owner_id, interaction.interaction_id
    )
    return turn.turn_id, interaction.interaction_id


def _count(db_path: Path, table: str) -> int:
    with connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_a_conversation_is_private_to_its_owner_and_a_delete_keeps_the_votes(
    settings: Settings, identity: IdentityIssuer
) -> None:
    owner = identity.user_id(ADA)
    db_path = settings.product_db_path
    with _client(settings, identity) as client:
        client.app.state.feedback.stop()
        identity.sign_in(client, ADA)
        created = client.post("/ask/chat/threads")
        assert created.status_code == 201, created.text
        thread_id = created.json()["thread_id"]
        turn_one, voted = _turn(settings, identity, thread_id, question="harbor budget")
        turn_two, _ = _turn(settings, identity, thread_id, question="lighthouse grant")
        other_thread = client.post("/ask/chat/threads").json()["thread_id"]

        listed = client.get("/ask/chat/threads")
        assert listed.status_code == 200, listed.text
        assert [item["thread_id"] for item in listed.json()] == [other_thread, thread_id]

        for accepted in ("Harbor", "h" * 120):
            renamed = client.patch(f"/ask/chat/threads/{thread_id}", json={"title": accepted})
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == accepted
        for refused_title in ("", "x" * 121):
            refused = client.patch(f"/ask/chat/threads/{thread_id}", json={"title": refused_title})
            assert refused.status_code in {400, 422}, refused.text
        unchanged = client.get(f"/ask/chat/threads/{thread_id}").json()["thread"]["title"]
        assert unchanged == "h" * 120

        detail = client.get(f"/ask/chat/threads/{thread_id}")
        assert detail.status_code == 200, detail.text
        turns = detail.json()["turns"]
        assert [turn["turn_id"] for turn in turns] == [turn_one, turn_two]
        assert [turn["seq"] for turn in turns] == [1, 2]
        assert turns[0]["grounding"] == "grounded"
        assert turns[0]["citations"] == [{"marker": 1, "rank": 1, "doc_id": "doc-a"}]
        assert turns[0]["answer"] == "The harbor budget was approved."
        assert turns[0]["question"] == "harbor budget"

        answer_vote = client.post(
            "/feedback",
            json={"interaction_id": voted, "target": "answer", "action": "up"},
        )
        assert answer_vote.status_code == 200, answer_vote.text
        for rank in (1, 2, 3):
            passage_vote = client.post(
                "/feedback",
                json={
                    "interaction_id": voted,
                    "target": "passage",
                    "rank": rank,
                    "action": "up",
                },
            )
            if rank <= 2:
                assert passage_vote.status_code == 200, passage_vote.text
            else:
                # Rank 3 is not in the turn's hits, so there is nothing to vote on.
                assert passage_vote.status_code == 400, passage_vote.text
        assert sync_pending(db_path) == 3

        # Another member's thread is 404 on every route, never 403 (NFR-4).
        identity.sign_in(client, BEA)
        for method, payload in (
            ("get", None),
            ("patch", {"title": "Mine now"}),
            ("delete", None),
        ):
            response = getattr(client, method)(
                f"/ask/chat/threads/{thread_id}", **({"json": payload} if payload else {})
            )
            assert response.status_code == 404, f"{method}: {response.text}"
        assert client.get("/ask/chat/threads").json() == []
        # A turn id is scoped the same way, so the turn routes ASKCHAT-004 adds
        # answer 404 for it (AC 17).
        with pytest.raises(KeyError):
            ask_chat.get_turn(db_path, turn_one, identity.user_id(BEA))

        # Unsigned is 401 on all five routes.
        identity.sign_out(client)
        assert client.get("/ask/chat/threads").status_code == 401
        assert client.post("/ask/chat/threads").status_code == 401
        assert client.get(f"/ask/chat/threads/{thread_id}").status_code == 401
        assert (
            client.patch(f"/ask/chat/threads/{thread_id}", json={"title": "x"}).status_code == 401
        )
        assert client.delete(f"/ask/chat/threads/{thread_id}").status_code == 401

        identity.sign_in(client, ADA)
        deleted = client.delete(f"/ask/chat/threads/{thread_id}")
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/ask/chat/threads/{thread_id}").status_code == 404

        # D-9: the conversation is gone and the evidence behind it is not.
        assert _count(db_path, "ask_chat_threads") == 1
        assert _count(db_path, "ask_chat_turns") == 0
        assert _count(db_path, "interactions") == 2
        assert _count(db_path, "interaction_hits") == 4
        assert _count(db_path, "feedback_events") == 3
        assert _count(db_path, "feedback_mirrors") == 3
        assert current_votes(db_path, owner, voted) == ("up", {1: "up", 2: "up"})
        assert [question.query for question in build_questions(db_path)] == ["harbor budget"]

        # The admin history is the feedback record, so it still shows them (D-9).
        identity.sign_in(client, ADMIN, "admin")
        history = client.get("/admin/interactions").json()["items"]
        assert {item["query"] for item in history} == {"harbor budget", "lighthouse grant"}

        # A turn left `running` is settled by the next process's lifespan (NFR-1).
        identity.sign_in(client, ADA)
        running_thread = client.post("/ask/chat/threads").json()["thread_id"]
    in_flight = ask_chat.open_turn(db_path, running_thread, owner, question="who approved it")
    assert in_flight.status == "running"
    with _client(settings, identity) as restarted:
        identity.sign_in(restarted, ADA)
        settled = restarted.get(f"/ask/chat/threads/{running_thread}").json()["turns"][0]
        assert settled["turn_id"] == in_flight.turn_id
        assert settled["status"] == "failed"
        assert settled["error"] == ask_chat.INTERRUPTED
        assert settled["ended_at"] is not None

        # D-11: there is no retention window, so age is not a lifecycle rule, and
        # the 50-item cap is a display cap rather than a delete.
        with connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO ask_chat_threads
                    (thread_id, title, owner_id, owner_email, created_at, updated_at)
                VALUES ('old-thread', 'From 2021', ?, ?, ?, ?)
                """,
                (owner, ADA, OLD_THREAD_AT, OLD_THREAD_AT),
            )
        assert "old-thread" in [
            item["thread_id"] for item in restarted.get("/ask/chat/threads").json()
        ]
        old = restarted.get("/ask/chat/threads/old-thread")
        assert old.status_code == 200, old.text
        assert old.json()["thread"]["updated_at"] == OLD_THREAD_AT

    many = [
        ask_chat.create_thread(db_path, owner, title=f"thread {index}").thread_id
        for index in range(60)
    ]
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        capped = client.get("/ask/chat/threads").json()
        past_the_cap = client.get(f"/ask/chat/threads/{many[0]}")
    assert len(capped) == 50
    assert many[0] not in {item["thread_id"] for item in capped}
    assert past_the_cap.status_code == 200, past_the_cap.text

    plans = {}
    with connect(db_path) as conn:
        for name, sql, params in (
            (
                "threads",
                f"EXPLAIN QUERY PLAN {ask_chat.THREADS_BY_OWNER_SQL}",
                (owner, ask_chat.LIST_LIMIT),
            ),
            (
                "turns",
                f"EXPLAIN QUERY PLAN {ask_chat.TURNS_BY_THREAD_SQL}",
                (running_thread, owner),
            ),
        ):
            plans[name] = " | ".join(str(row["detail"]) for row in conn.execute(sql, params))
    assert "ask_chat_threads_by_owner" in plans["threads"]
    assert "ask_chat_turns_by_thread" in plans["turns"]
    assert "SCAN" not in plans["threads"]
    assert "SCAN" not in plans["turns"]


def test_the_schema_is_idempotent_and_checks_its_statuses(settings: Settings) -> None:
    db_path = settings.product_db_path
    ask_chat.init_ask_chat(db_path)
    with connect(db_path) as conn:
        before = [
            tuple(row)
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )
        ]
    ask_chat.init_ask_chat(db_path)
    with connect(db_path) as conn:
        after = [
            tuple(row)
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )
        ]
        thread_columns = [
            str(row["name"]) for row in conn.execute("PRAGMA table_info(ask_chat_threads)")
        ]
        turn_columns = [
            str(row["name"]) for row in conn.execute("PRAGMA table_info(ask_chat_turns)")
        ]
        interactions = [
            str(row["name"]) for row in conn.execute("PRAGMA table_info(interactions)")
        ]
    assert before == after
    assert thread_columns == [
        "thread_id",
        "title",
        "owner_id",
        "owner_email",
        "created_at",
        "updated_at",
    ]
    assert turn_columns == [
        "turn_id",
        "thread_id",
        "owner_id",
        "seq",
        "status",
        "retry_of",
        "error",
        "grounding",
        "citations",
        "retrieval_query",
        "first_token_ms",
        "started_at",
        "ended_at",
    ]
    assert {"thread_id", "turn_id"} <= set(interactions)

    thread = ask_chat.create_thread(db_path, "owner-1")
    # AC 3: the CHECK, not just the Python guard, rejects an unknown status.
    with pytest.raises(sqlite3.IntegrityError), connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ask_chat_turns
                (turn_id, thread_id, owner_id, seq, status, started_at)
            VALUES ('t', ?, 'owner-1', 9, 'unknown', ?)
            """,
            (thread.thread_id, datetime.now(timezone.utc).isoformat()),
        )
    with pytest.raises(ValueError):
        ask_chat.append_turn(db_path, thread.thread_id, "owner-1", status="unknown")
    # D-5: a chat is an `ask`, so the kind CHECK keeps rejecting a new kind.
    with pytest.raises(sqlite3.IntegrityError), connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO interactions
                (interaction_id, kind, user_id, query, build_id, config_path, created_at)
            VALUES ('i', 'ask_chat', 'owner-1', 'q', 'b', 'c', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
    # D-3: grounding is the landed enum, and no fourth status is storable.
    with pytest.raises(sqlite3.IntegrityError), connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ask_chat_turns
                (turn_id, thread_id, owner_id, seq, status, grounding, started_at)
            VALUES ('t', ?, 'owner-1', 9, 'completed', 'partly_grounded', ?)
            """,
            (thread.thread_id, datetime.now(timezone.utc).isoformat()),
        )

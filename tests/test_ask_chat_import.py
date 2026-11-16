"""Importing the browser's old conversations, once, without inventing anything.

Real app, real SQLite file. The cases are the story's four: two members importing
overlapping interaction ids each keep only their own; a re-run is a no-op; a
session whose interaction is gone imports its thread with the turn skipped and
counted; and a turn with no ``interactionId`` is counted as skipped rather than
stored as an orphan.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
import mlflow
import pytest

from deepfix_rag import ask_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.interactions import get_interaction, record_interaction
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]

ADA = "ada@example.com"
BEA = "bea@example.com"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return Settings(
        _env_file=None,
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(
        create_app(
            settings,
            embed_model=MockEmbedding(embed_dim=16),
            llm=MockLLM(),
            token_verifier=identity.verifier(),
        )
    )


def _interaction(settings: Settings, identity: IdentityIssuer, email: str, question: str) -> str:
    return record_interaction(
        settings.product_db_path,
        kind="ask",
        user_id=identity.user_id(email),
        user_email=email,
        query=question,
        build_id="build-1",
        config_path="configs/champion.yaml",
        results=[RetrievalResult(doc_id="doc-a", score=0.9, text="the harbor budget")],
        answer="The harbor budget was approved.",
    ).interaction_id


def _session(session_id: str, *, turns: list[dict[str, object]], title: str = "Alt"):
    return {
        "id": session_id,
        "title": title,
        "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-02T00:00:00+00:00",
        "turns": turns,
    }


def test_two_members_importing_overlapping_ids_keep_only_their_own(
    settings: Settings, identity: IdentityIssuer
) -> None:
    """AC 2, 5: the ownership check is the security boundary of this route."""
    ada_interaction = _interaction(settings, identity, ADA, "ada question")
    bea_interaction = _interaction(settings, identity, BEA, "bea question")
    payload = {
        "sessions": [
            _session(
                "session-1",
                turns=[
                    {"interactionId": ada_interaction, "userQuery": "ada question"},
                    {"interactionId": bea_interaction, "userQuery": "bea question"},
                ],
            )
        ]
    }
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        imported = client.post("/ask/chat/threads/import", json=payload)
        assert imported.status_code == 200, imported.text
        body = imported.json()
        # Ada keeps her own turn; Bea's is skipped rather than attached to her.
        assert body == {"threads_created": 1, "turns_created": 1, "turns_skipped": 1}
        threads = client.get("/ask/chat/threads").json()
        assert len(threads) == 1
        detail = client.get(f"/ask/chat/threads/{threads[0]['thread_id']}").json()
        assert [turn["interaction_id"] for turn in detail["turns"]] == [ada_interaction]

        identity.sign_in(client, BEA)
        assert client.get("/ask/chat/threads").json() == []
        bea = client.post(
            "/ask/chat/threads/import",
            json={
                "sessions": [
                    _session(
                        "session-1",
                        turns=[{"interactionId": bea_interaction, "userQuery": "bea question"}],
                    )
                ]
            },
        )
        assert bea.json() == {"threads_created": 1, "turns_created": 1, "turns_skipped": 0}


def test_a_re_run_is_a_no_op(settings: Settings, identity: IdentityIssuer) -> None:
    """AC 4: idempotent per session id and per interaction id, not per payload."""
    interaction = _interaction(settings, identity, ADA, "ada question")
    session = _session(
        "session-1", turns=[{"interactionId": interaction, "userQuery": "ada question"}]
    )
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        first = client.post("/ask/chat/threads/import", json={"sessions": [session]})
        second = client.post("/ask/chat/threads/import", json={"sessions": [session]})
        third = client.post(
            "/ask/chat/threads/import",
            json={
                "sessions": [
                    _session(
                        "session-2",
                        turns=[{"interactionId": interaction, "userQuery": "ada question"}],
                    )
                ]
            },
        )
    assert first.json() == {"threads_created": 1, "turns_created": 1, "turns_skipped": 0}
    # The same session again creates nothing: the thread is already there.
    assert second.json() == {"threads_created": 0, "turns_created": 0, "turns_skipped": 0}
    # A different session naming the same interaction imports its thread and skips
    # the turn, because that interaction is already one turn's.
    assert third.json() == {"threads_created": 1, "turns_created": 0, "turns_skipped": 0}
    with connect(settings.product_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ask_chat_threads").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM ask_chat_turns").fetchone()[0] == 1


def test_a_missing_interaction_and_a_missing_id_are_counted_not_invented(
    settings: Settings, identity: IdentityIssuer
) -> None:
    """AC 2, 3: nothing is fabricated; the thread is kept and the counts say so."""
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        imported = client.post(
            "/ask/chat/threads/import",
            json={
                "sessions": [
                    _session(
                        "session-1",
                        turns=[
                            {"interactionId": "does-not-exist", "userQuery": "gone"},
                            {"userQuery": "no id at all"},
                        ],
                    )
                ]
            },
        )
        body = imported.json()
        threads = client.get("/ask/chat/threads").json()
        detail = client.get(f"/ask/chat/threads/{threads[0]['thread_id']}").json()
    assert body == {"threads_created": 1, "turns_created": 0, "turns_skipped": 2}
    assert detail["turns"] == []
    # No interaction was invented for either turn.
    with connect(settings.product_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 0


def test_the_import_refuses_an_unbounded_payload_and_an_unsigned_caller(
    settings: Settings, identity: IdentityIssuer
) -> None:
    """AC 6: 401 unsigned, 400 naming the bound for a list past it."""
    with _client(settings, identity) as client:
        unsigned = client.post("/ask/chat/threads/import", json={"sessions": []})
        assert unsigned.status_code == 401
        identity.sign_in(client, ADA)
        too_many = client.post(
            "/ask/chat/threads/import",
            json={"sessions": [_session(f"s-{index}", turns=[]) for index in range(201)]},
        )
        assert too_many.status_code == 400, too_many.text
        empty = client.post("/ask/chat/threads/import", json={"sessions": []})
        assert empty.status_code == 200, empty.text
        assert empty.json() == {"threads_created": 0, "turns_created": 0, "turns_skipped": 0}


def test_an_imported_turn_can_be_opened_and_reports_its_stored_answer(
    settings: Settings, identity: IdentityIssuer
) -> None:
    """The thread an import creates is a normal thread, not a special case."""
    interaction = _interaction(settings, identity, ADA, "ada question")
    assert get_interaction(settings.product_db_path, interaction) is not None
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        client.post(
            "/ask/chat/threads/import",
            json={
                "sessions": [
                    _session(
                        "session-1",
                        title="Harbor",
                        turns=[{"interactionId": interaction, "userQuery": "ada question"}],
                    )
                ]
            },
        )
        threads = client.get("/ask/chat/threads").json()
        detail = client.get(f"/ask/chat/threads/{threads[0]['thread_id']}").json()
    assert threads[0]["title"] == "Harbor"
    turn = detail["turns"][0]
    assert turn["question"] == "ada question"
    assert turn["answer"] == "The harbor budget was approved."
    assert turn["status"] == "completed"
    assert len(turn["passages"]) == 1
    assert ask_chat.__doc__ is not None


def test_the_retired_page_payload_is_accepted_as_it_was_written(
    settings: Settings, identity: IdentityIssuer
) -> None:
    """The browser's own payload shape must import, not be rejected with a 422.

    The retired page wrote `createdAt` and `updatedAt` as `Date.now()` **numbers**.
    A strict string field rejected every one of them, and the web app turned that
    422 into "Your earlier conversations could not be carried over" — a scary
    sentence for a payload the server should simply have accepted. Both shapes are
    taken, and the numeric one is normalised to an ISO string.
    """
    interaction = _interaction(settings, identity, ADA, "old question")
    payload = {
        "sessions": [
            {
                "id": "local-session-1",
                "title": "Alte Unterhaltung",
                # Exactly what `Date.now()` produces, as a JSON number.
                "createdAt": 1700000000000,
                "updatedAt": 1700003600000,
                "turns": [
                    {"interactionId": interaction, "userQuery": "old question", "answer": "a"}
                ],
            }
        ]
    }
    with _client(settings, identity) as client:
        identity.sign_in(client, ADA)
        imported = client.post("/ask/chat/threads/import", json=payload)
        assert imported.status_code == 200, imported.text
        assert imported.json() == {"threads_created": 1, "turns_created": 1, "turns_skipped": 0}
        threads = client.get("/ask/chat/threads").json()
        detail = client.get(f"/ask/chat/threads/{threads[0]['thread_id']}").json()
    assert threads[0]["title"] == "Alte Unterhaltung"
    # The numeric timestamp became an ISO string, and the thread keeps its age.
    assert threads[0]["created_at"].startswith("2023-11-14T")
    assert [turn["question"] for turn in detail["turns"]] == ["old question"]

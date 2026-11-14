"""Ask stores one answer and the passages it used, even when generation fails."""

from fastapi.testclient import TestClient
from llama_index.core.base.llms.types import ChatMessage, ChatResponse, CompletionResponse
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
import mlflow
import pytest

from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.interactions import get_interaction
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]


prompts: list[str] = []


class GroundedLLM(MockLLM):
    """Local model that returns one fixed answer and records each prompt."""

    def complete(self, prompt: str, formatted: bool = False, **kwargs) -> CompletionResponse:
        prompts.append(prompt)
        return CompletionResponse(text="The harbor budget was approved.")

    def chat(self, messages, **kwargs) -> ChatResponse:
        prompts.append("\n".join(str(message.content) for message in messages))
        return ChatResponse(
            message=ChatMessage(role="assistant", content="The harbor budget was approved.")
        )


class FailingLLM(MockLLM):
    """Local model whose generation always fails after retrieval."""

    def complete(self, prompt: str, formatted: bool = False, **kwargs) -> CompletionResponse:
        raise RuntimeError("generation failed")

    def chat(self, messages, **kwargs) -> ChatResponse:
        raise RuntimeError("generation failed")


@pytest.fixture
def ask_settings(tmp_path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
    )


def _client(settings: Settings, llm: MockLLM, identity: IdentityIssuer) -> TestClient:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    app = create_app(
        settings,
        embed_model=MockEmbedding(embed_dim=16),
        llm=llm,
        token_verifier=identity.verifier(),
    )
    return TestClient(app)


def _index_note(client: TestClient) -> None:
    uploaded = client.post(
        "/documents",
        files={"file": ("harbor.txt", b"The harbor budget was approved.", "text/plain")},
    )
    assert uploaded.status_code == 201
    client.app.state.index.wait()


def test_ask_stores_the_answer_and_a_single_turn(
    ask_settings: Settings, identity: IdentityIssuer
) -> None:
    prompts.clear()
    with _client(ask_settings, GroundedLLM(), identity) as client:
        identity.sign_in(client, "ada@example.com")
        _index_note(client)
        prompts.clear()
        first = client.post("/ask", json={"query": "harbor budget"})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["answer"] == "The harbor budget was approved."
        assert body["error"] is None
        assert body["vote"] is None
        assert body["hits"]
        assert body["hits"][0]["document_filename"] == "harbor.txt"
        assert body["hits"][0]["vote"] is None
        stored = get_interaction(ask_settings.product_db_path, body["interaction_id"])
        assert stored is not None
        assert stored.kind == "ask"
        assert stored.answer == body["answer"]
        assert stored.trace_id
        trace = mlflow.get_trace(stored.trace_id, flush=True)
        assert trace is not None
        assert trace.info.tags["interaction_id"] == stored.interaction_id

        before_second = len(prompts)
        second = client.post("/ask", json={"query": "lighthouse"})
        assert second.status_code == 200
        assert second.json()["interaction_id"] != body["interaction_id"]
        later = prompts[before_second:]
        assert later
        assert any("lighthouse" in prompt for prompt in later)
        assert all(
            "harbor budget" not in prompt.split("The harbor budget was approved.")[0]
            for prompt in later
        )


def test_generation_failure_keeps_the_passages(
    ask_settings: Settings, identity: IdentityIssuer
) -> None:
    with _client(ask_settings, FailingLLM(), identity) as client:
        identity.sign_in(client, "ada@example.com")
        empty = client.post("/ask", json={"query": "   "})
        assert empty.status_code == 400
        missing = client.post("/ask", json={"query": "harbor"})
        assert missing.status_code == 503
        _index_note(client)
        failed = client.post("/ask", json={"query": "harbor budget"})
        assert failed.status_code == 200, failed.text
        body = failed.json()
        assert body["answer"] is None
        assert body["error"] == "The answer could not be generated."
        assert body["hits"]
        stored = get_interaction(ask_settings.product_db_path, body["interaction_id"])
        assert stored is not None
        assert stored.kind == "ask"
        assert stored.answer is None
        assert stored.error == body["error"]
        assert stored.hits

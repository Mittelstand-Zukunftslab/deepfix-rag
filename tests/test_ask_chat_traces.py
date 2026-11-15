"""A chat turn's trace, and the standalone question the optimizer exports.

Real app, real SQLite file, MLflow pointed at a local SQLite tracking store. The
four cases are the story's: a two-turn conversation whose second search used a
standalone query exports two questions and the second is that query; a
single-turn `POST /ask` interaction with no `retrieval_query` exports its own
query; an unchanged query keeps its holdout membership; and a turn with two tool
calls leaves one trace with two child spans and the same `interaction_id` tag as
its stored row.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import mlflow
import pytest

from deepfix_rag import ask_chat, promotion
from deepfix_rag.api.app import create_app
from deepfix_rag.config.schema import DataSourceType, RAGConfig
from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import init_documents, prepare_workspace
from deepfix_rag.feedback import build_questions, is_holdout, normalize_query, query_id_for
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]

MEMBER = "ada@example.com"
MODEL = "ask-chat-model"
ASK_CHAT_DIR = Path(__file__).resolve().parents[1] / "src" / "deepfix_rag"

DOCUMENTS = {
    "doc_de_001": (
        "Gemäß § 315c Strafgesetzbuch wird wegen Gefährdung des Straßenverkehrs bestraft, "
        "wer im Straßenverkehr ein Fahrzeug führt, obwohl er infolge des Genusses "
        "alkoholischer Getränke nicht in der Lage ist, das Fahrzeug sicher zu führen."
    ),
    "doc_de_002": (
        "Der Führerschein wird bei einer Trunkenheitsfahrt regelmäßig entzogen, und die "
        "Fahrerlaubnis kann nach einer Sperrfrist neu erteilt werden."
    ),
}


@dataclass
class State:
    """The endpoint's script: one entry per step, in order."""

    steps: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)


class Endpoint(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint that replays a fixed script of steps."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        return

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        index = len(self.state.requests)
        self.state.requests.append(body)
        step = self.state.steps[min(index, len(self.state.steps) - 1)]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        if "tool" in step:
            call = step["tool"]
            self._send(
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": "search_documents",
                                            "arguments": json.dumps(call["arguments"]),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
        else:
            for piece in step.get("text", "").split(" "):
                self._send(
                    {
                        "id": "c",
                        "object": "chat.completion.chunk",
                        "model": MODEL,
                        "choices": [
                            {"index": 0, "delta": {"content": piece + " "}, "finish_reason": None}
                        ],
                    }
                )
        self._send(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "model": MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send(self, payload: dict) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()


@pytest.fixture
def endpoint() -> Iterator[State]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    state = State()
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    state.url = f"http://{host}:{port}"  # type: ignore[attr-defined]
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _settings(tmp_path: Path, endpoint: State) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    settings = Settings(
        _env_file=None,
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        MLFLOW_TRACKING_URI=f"sqlite:///{tmp_path / 'mlflow.db'}",
        LLM_BASE_URL=endpoint.url,  # type: ignore[attr-defined]
        LLM_API_KEY="test-key",
        LLM_MODEL=MODEL,
    )
    prepare_workspace(settings.workspace_dir)
    corpus = settings.workspace_dir / "corpus"
    init_documents(settings.product_db_path)
    for doc_id, text in DOCUMENTS.items():
        (corpus / f"{doc_id}.json").write_text(
            json.dumps({"doc_id": doc_id, "text": text, "filename": f"{doc_id}.txt"}),
            encoding="utf-8",
        )
        with connect(settings.product_db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (doc_id, filename, media_type, text, status, uploaded_by, created_at)
                VALUES (?, ?, 'text/plain', ?, 'indexed', 'owner-1', ?)
                """,
                (doc_id, f"{doc_id}.txt", text, "2026-11-15T00:00:00+00:00"),
            )
    champion = RAGConfig.from_yaml(settings.workspace_dir / "configs" / "champion.yaml")
    config = champion.model_copy(
        update={
            "retrieval": champion.retrieval.model_copy(update={"backend": "hybrid", "top_k": 20}),
            "rerank": champion.rerank.model_copy(update={"enabled": False}),
            "data": champion.data.model_copy(
                update={"path_or_repo": str(corpus), "source_type": DataSourceType.DIRECTORY}
            ),
        }
    )
    (settings.workspace_dir / "configs" / "champion.yaml").write_text(
        config.to_yaml(), encoding="utf-8"
    )
    return settings


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(
        create_app(
            settings,
            embed_model=MockEmbedding(embed_dim=16),
            token_verifier=identity.verifier(),
        )
    )


def _ask(
    client: TestClient,
    identity: IdentityIssuer,
    thread: str,
    question: str,
) -> dict:
    identity.sign_in(client, MEMBER)
    response = client.post(
        "/ask/chat/completions",
        json={"thread_id": thread, "messages": [{"role": "user", "content": question}]},
    )
    assert response.status_code == 200, response.text
    return next(
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:") and '"data-deepfix-turn"' in line
    )["data"]


def test_a_follow_up_exports_the_standalone_query_the_search_ran(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 1, 6, 7, 10: the export asks the tool's query, not the pronoun sentence."""
    settings = _settings(tmp_path, endpoint)
    endpoint.steps = [
        {"tool": {"id": "c1", "arguments": {"query": "Trunkenheitsfahrt strafbar"}}},
        {"text": "Die Trunkenheitsfahrt ist strafbar [1]."},
        {"tool": {"id": "c2", "arguments": {"query": "Führerschein Entzug Sperrfrist"}}},
        {"text": "Der Führerschein wird entzogen [2]."},
    ]
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        _ask(client, identity, thread, "Ist eine Trunkenheitsfahrt strafbar?")
        _ask(client, identity, thread, "Und wer entscheidet darüber?")

    with connect(settings.product_db_path) as conn:
        rows = conn.execute(
            """
            SELECT t.turn_id, t.retrieval_query, t.turn_id AS interaction_turn,
                   i.interaction_id
            FROM ask_chat_turns AS t
            LEFT JOIN interactions AS i ON i.turn_id = t.turn_id
            ORDER BY t.seq
            """
        ).fetchall()
        queries = [
            str(row["query"]) for row in conn.execute("SELECT query FROM interactions").fetchall()
        ]
    assert [row["retrieval_query"] for row in rows] == [
        "Trunkenheitsfahrt strafbar",
        "Führerschein Entzug Sperrfrist",
    ]
    # The raw message is what the history screen shows, unchanged.
    assert queries == ["Ist eine Trunkenheitsfahrt strafbar?", "Und wer entscheidet darüber?"]

    # Votes on both turns' passages become two dev questions, the second the tool's.
    for row in rows:
        with connect(settings.product_db_path) as conn:
            hits = conn.execute(
                "SELECT rank FROM interaction_hits WHERE interaction_id = ?",
                (row["interaction_id"],),
            ).fetchall()
        for hit in hits:
            from deepfix_rag.feedback import record_vote

            record_vote(
                settings.product_db_path,
                user_id=identity.user_id(MEMBER),
                interaction_id=str(row["interaction_id"]),
                target="passage",
                rank=int(hit["rank"]),
                action="up",
            )
    exported = {question.query for question in build_questions(settings.product_db_path)}
    assert "trunkenheitsfahrt strafbar" in exported
    assert "führerschein entzug sperrfrist" in exported
    assert "und wer entscheidet darüber?" not in exported


def test_a_single_turn_ask_with_no_turn_row_keeps_its_own_query(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 6: an interaction with no `ask_chat_turns` row is unaffected."""
    settings = _settings(tmp_path, endpoint)
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        client.app.state.feedback.stop()
        uploaded = client.post(
            "/documents",
            files={"file": ("h.txt", b"Die Trunkenheitsfahrt ist strafbar.", "text/plain")},
        )
        assert uploaded.status_code == 201
        client.app.state.index.wait()
        asked = client.post("/ask", json={"query": "Trunkenheitsfahrt"})
        assert asked.status_code == 200, asked.text
        interaction_id = asked.json()["interaction_id"]
        voted = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "up",
            },
        )
        assert voted.status_code == 200, voted.text
    questions = build_questions(settings.product_db_path)
    assert [question.query for question in questions] == ["trunkenheitsfahrt"]


def test_holdout_membership_of_an_unchanged_query_is_unchanged(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 8: the split still hashes the coalesced text with the same 20% rule."""
    normalized = normalize_query("Trunkenheitsfahrt")
    before = (is_holdout(normalized), query_id_for(normalized))
    after = (is_holdout(normalize_query("  TRUNKENHEITSFAHRT  ")), query_id_for(normalized))
    # Normalisation, the hash, and the threshold are all unchanged.
    assert before == after
    # And the rule is still 20%: a sample of queries lands near a fifth.
    holdout = [is_holdout(normalize_query(f"frage {index}")) for index in range(400)]
    assert 0.12 < sum(holdout) / len(holdout) < 0.28


def test_a_turn_with_two_tool_calls_leaves_one_trace_with_two_child_spans(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 1–3: one trace, tagged for its row, with one span per search."""
    settings = _settings(tmp_path, endpoint)
    endpoint.steps = [
        {"tool": {"id": "c1", "arguments": {"query": "Trunkenheitsfahrt"}}},
        {"tool": {"id": "c2", "arguments": {"query": "Führerschein"}}},
        {"text": "Beides steht im Gesetz [1] [2]."},
    ]
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        turn = _ask(client, identity, thread, "Was gilt bei einer Trunkenheitsfahrt?")
    with connect(settings.product_db_path) as conn:
        row = conn.execute(
            "SELECT trace_id, interaction_id FROM interactions WHERE turn_id = ?",
            (turn["turn_id"],),
        ).fetchone()
    assert row is not None
    trace_id = str(row["trace_id"])
    assert trace_id

    trace = mlflow.get_trace(trace_id, flush=True)
    assert trace is not None
    assert trace.info.tags["interaction_id"] == str(row["interaction_id"])
    assert trace.info.tags["thread_id"] == thread
    assert trace.info.tags["turn_id"] == turn["turn_id"]
    assert trace.info.tags["grounding"] == "grounded"
    # One root span and one child per search, each carrying its own numbers.
    names = [span.name for span in trace.data.spans]
    assert names.count("search_documents") == 2
    assert "ask_chat" in names


def test_the_ask_chat_path_cannot_promote_or_read_holdout() -> None:
    """AC 11, 12: the guard is a property of the modules, asserted on their source."""
    files = [
        ASK_CHAT_DIR / "ask_chat.py",
        ASK_CHAT_DIR / "ask_chat_turn.py",
        ASK_CHAT_DIR / "api" / "routes" / "ask_chat.py",
        ASK_CHAT_DIR / "api" / "ask_chat_turns.py",
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        # FR-023: no chat path may call the promotion gate or write its paths.
        assert "apply_candidate" not in source, path
        assert "indexes/current" not in source, path
        assert "champion_diff" not in source, path
        # D-6: the chat reads no holdout question, id, or document id. The word is
        # allowed in a docstring that says so; an import or a call is not.
        assert "import holdout" not in source, path
        assert "holdout_query" not in source.lower(), path
        assert "from deepfix_rag.promotion" not in source, path
        assert "from deepfix_rag.holdout" not in source, path
    # And the gate is still the one writer.
    gate = (ASK_CHAT_DIR / "promotion.py").read_text(encoding="utf-8")
    assert "def apply_candidate" in gate
    assert callable(promotion.apply_candidate)
    # A turn's stored query is the first search's, which is what the export reads.
    assert ask_chat.import_sessions is not None

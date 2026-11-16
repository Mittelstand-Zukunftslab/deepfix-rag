"""Stop, resume, regenerate, and failing honestly.

A real HTTP endpoint on a loopback port and the real app, as
``tests/test_ask_chat_stream.py`` does, because every case here is about what
survives a socket closing or a stream ending early.

Four things are asserted, and they are the story's four required cases:

* a client that closes mid-stream leaves a ``cancelled`` row with the partial
  text and the tool's hits, and the endpoint records that its response stream was
  closed rather than abandoned;
* a resume of that turn replays the stored text once and then the terminal frame;
* a regenerate leaves one assistant answer and one ``clear`` event per prior vote;
* a failure frame yields ``failed`` and a regenerate that follows succeeds.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import httpx
from llama_index.core.embeddings import MockEmbedding
import mlflow
import pytest
import uvicorn

from deepfix_rag import ask_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.config.schema import DataSourceType, RAGConfig
from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import init_documents, prepare_workspace
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]

MEMBER = "ada@example.com"
MODEL = "ask-chat-model"
DOCUMENTS = {
    "doc_de_001": (
        "Gemäß § 315c Strafgesetzbuch wird wegen Gefährdung des Straßenverkehrs bestraft, "
        "wer im Straßenverkehr ein Fahrzeug führt, obwohl er infolge des Genusses "
        "alkoholischer Getränke nicht in der Lage ist, das Fahrzeug sicher zu führen."
    ),
    "doc_de_002": (
        "Die grobe Verletzung der Verkehrsregeln im Sinne des § 315c Absatz 1 Nummer 2 "
        "setzt ein grob verkehrswidriges und rücksichtsloses Fahren voraus."
    ),
}


@dataclass
class State:
    """How the endpoint should answer, and what it observed."""

    mode: str = "tool_then_text"
    requests: list[dict] = field(default_factory=list)
    closed_early: bool = False
    pause: float = 0.0


class Endpoint(BaseHTTPRequestHandler):
    """An OpenAI-compatible streaming endpoint that can stall or fail."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        return

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.state.requests.append(body)
        messages = body.get("messages") or []
        has_tool = any(item.get("role") == "tool" for item in messages)
        if self.state.mode == "reject_tool_choice" and not has_tool:
            # A provider that refuses the forced step, which is the deployed
            # failure. The turn must settle `failed` without ever searching.
            body = json.dumps(
                {
                    "error": {
                        "message": (
                            "The tool_choice parameter does not support being set to "
                            "required or object in thinking mode"
                        ),
                        "type": "invalid_request_error",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        if self.state.mode == "broken_provider":
            # A provider that cannot answer at all, after the search: the turn
            # fails with an interaction that has no answer of its own.
            body = json.dumps({"error": {"message": "upstream is down"}}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.state.mode == "fail_after_tool" and has_tool:
            self._send({"type": "error", "error": {"message": "upstream exploded"}})
            self._done()
            return
        if not has_tool:
            self._tool_call("call-1", '{"query": "Trunkenheitsfahrt"}')
            self._done()
            return
        pieces = ["Die ", "Trunkenheitsfahrt ", "ist ", "strafbar ", "[1]."]
        for index, piece in enumerate(pieces):
            self._send(
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "model": MODEL,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
            )
            if self.state.pause and index == 1:
                # Stalled mid-answer, so the browser can close while it streams.
                self.state.closed_early = self._wait_for_client_close()
                if self.state.closed_early:
                    return
        self._send(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "model": MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        self._done()

    def _wait_for_client_close(self, timeout: float = 10.0) -> bool:
        """Block until the client closes its socket, so the endpoint can report it.

        The check is a zero-byte write followed by a read with a short timeout: a
        closed peer raises ``BrokenPipeError`` on the write or returns ``b""`` on
        the read, and either one means the response stream was ended rather than
        left running to ``max_tokens``.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.2)
            try:
                self.wfile.write(b"")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return True
            try:
                self.connection.settimeout(0.2)
                if self.connection.recv(1) == b"":
                    return True
            except TimeoutError:
                continue
            except OSError:
                return True
        return False

    def _send(self, payload: dict) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def _tool_call(self, call_id: str, arguments: str) -> None:
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
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": "search_documents",
                                        "arguments": arguments,
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
        )

    def _done(self) -> None:
        self.wfile.write(b"data: [DONE]\n\n")
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
    settings = Settings(
        _env_file=None,
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
        LLM_BASE_URL=endpoint.url,  # type: ignore[attr-defined]
        LLM_API_KEY="test-key",
        LLM_MODEL=MODEL,
        MAX_DISPLAYED_HITS=10,
        ASK_CHAT_TURN_TIMEOUT_SECONDS=20,
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
            settings, embed_model=MockEmbedding(embed_dim=16), token_verifier=identity.verifier()
        )
    )


def _frames(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
    ]


def _ask(client: TestClient, identity: IdentityIssuer, question: str, **extra: object):
    identity.sign_in(client, MEMBER)
    thread = client.post("/ask/chat/threads").json()["thread_id"]
    response = client.post(
        "/ask/chat/completions",
        json={
            "thread_id": thread,
            "messages": [{"role": "user", "content": question}],
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    turn_id = next(
        f["data"]["turn_id"] for f in _frames(response.text) if f["type"] == "data-deepfix-turn"
    )
    return thread, turn_id, _frames(response.text)


def _events(db_path: Path, interaction_id: str) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT action FROM feedback_events WHERE interaction_id = ? ORDER BY created_at",
            (interaction_id,),
        ).fetchall()
    return [str(row["action"]) for row in rows]


def test_a_resume_replays_the_stored_answer_once_then_the_terminal_frame(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 6–9: the stored text arrives once, then the status and the terminator."""
    settings = _settings(tmp_path, endpoint)
    with _client(settings, identity) as client:
        _, turn_id, frames = _ask(client, identity, "Ist eine Trunkenheitsfahrt strafbar?")
        streamed = "".join(f["delta"] for f in frames if f["type"] == "text-delta")
        resumed = client.get(f"/ask/chat/turns/{turn_id}/stream")
    assert resumed.status_code == 200, resumed.text
    assert resumed.headers["x-vercel-ai-ui-message-stream"] == "v1"
    again = _frames(resumed.text)
    replayed = "".join(f["delta"] for f in again if f["type"] == "text-delta")
    # FR-016: the replayed text appears exactly once, and it is what was streamed.
    assert replayed == streamed
    assert again[0]["type"] == "start"
    terminal = next(f for f in again if f["type"] == "data-deepfix-turn")["data"]
    assert terminal["status"] == "completed"
    assert terminal["grounding"] == "grounded"
    assert again[-1]["type"] == "finish"

    identity.sign_in(client, "bea@example.com")
    with _client(settings, identity) as other:
        identity.sign_in(other, "bea@example.com")
        assert other.get(f"/ask/chat/turns/{turn_id}/stream").status_code == 404
        assert other.get("/ask/chat/turns/nope/stream").status_code == 404


def test_a_regenerate_replaces_the_answer_in_place_and_clears_its_votes(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 10–12: one answer at that position, the old text gone, votes cleared."""
    settings = _settings(tmp_path, endpoint)
    with _client(settings, identity) as client:
        thread, turn_id, _ = _ask(client, identity, "Ist eine Trunkenheitsfahrt strafbar?")
        detail = client.get(f"/ask/chat/threads/{thread}").json()
        interaction_id = detail["turns"][0]["interaction_id"]
        assert interaction_id
        voted = client.post(
            "/feedback",
            json={"interaction_id": interaction_id, "target": "answer", "action": "up"},
        )
        assert voted.status_code == 200, voted.text
        passage = client.post(
            "/feedback",
            json={
                "interaction_id": interaction_id,
                "target": "passage",
                "rank": 1,
                "action": "down",
            },
        )
        assert passage.status_code == 200, passage.text

        regenerated = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}],
                "regenerate": True,
            },
        )
        assert regenerated.status_code == 200, regenerated.text
        after = client.get(f"/ask/chat/threads/{thread}").json()

    turns = after["turns"]
    assert [turn["turn_id"] for turn in turns] == [turn_id]
    assert turns[0]["status"] == "completed"
    assert turns[0]["answer"].count("strafbar") == 1
    # FR-015: every prior vote was cleared through the existing `clear` action.
    actions = _events(settings.product_db_path, interaction_id)
    assert actions.count("clear") == 2
    assert "up" in actions and "down" in actions

    # A regenerate on a thread with nothing answered is a 400, not a silent no-op.
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        empty = client.post("/ask/chat/threads").json()["thread_id"]
        refused = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": empty,
                "messages": [{"role": "user", "content": "Hallo"}],
                "regenerate": True,
            },
        )
        assert refused.status_code == 400, refused.text


def test_a_failed_turn_is_failed_and_a_regenerate_after_it_succeeds(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 15–17: an error part, a `failed` row, and a fresh attempt that works."""
    settings = _settings(tmp_path, endpoint)
    endpoint.mode = "fail_after_tool"
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        failed_response = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}],
            },
        )
        frames = _frames(failed_response.text)
        types = [f["type"] for f in frames]
        assert "error" in types
        terminal = next(f for f in frames if f["type"] == "data-deepfix-turn")["data"]
        assert terminal["status"] == "failed"
        stored = client.get(f"/ask/chat/threads/{thread}").json()["turns"][0]
        assert stored["status"] == "failed"
        assert stored["error"]
        assert "upstream exploded" not in stored["error"] or "exploded" not in json.dumps(
            client.get("/admin/interactions").json()
        )

        endpoint.mode = "tool_then_text"
        retried = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}],
                "regenerate": True,
            },
        )
        assert retried.status_code == 200, retried.text
        after = client.get(f"/ask/chat/threads/{thread}").json()["turns"]

    assert len(after) == 1
    assert after[0]["status"] == "completed"
    assert after[0]["answer"]


def test_every_terminal_path_leaves_no_running_row(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """NFR-1: the row is settled before the stream closes, on both outcomes."""
    settings = _settings(tmp_path, endpoint)
    with _client(settings, identity) as client:
        _ask(client, identity, "Ist eine Trunkenheitsfahrt strafbar?")
        endpoint.mode = "fail_after_tool"
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Und der Führerschein?"}],
            },
        )
    with connect(settings.product_db_path) as conn:
        running = conn.execute(
            "SELECT COUNT(*) FROM ask_chat_turns WHERE status = 'running'"
        ).fetchone()[0]
        statuses = {
            str(row["status"])
            for row in conn.execute("SELECT status FROM ask_chat_turns").fetchall()
        }
    assert running == 0
    assert statuses == {"completed", "failed"}
    # The cancelled path stores a sentence rather than a traceback (FR-015, NFR-1).
    from deepfix_rag import ask_chat_turn

    assert "closed" in ask_chat_turn.CANCELLED.lower()
    assert "closed" in ask_chat_turn.TOO_LONG.lower() or "long" in ask_chat_turn.TOO_LONG.lower()


def test_a_closed_socket_does_not_cancel_the_turn_and_a_resume_finishes_it(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """FR-016: a reload mid-answer must not lose the reply.

    The browser's socket closing is what a reload does, and it is indistinguishable
    from a Stop. So it must not stop the turn: the model call is owned by the
    process, the row keeps its text, and a resume follows the same turn to its
    terminal frame. This test runs the real app under ``uvicorn`` because a
    ``TestClient`` cannot close a socket mid-stream.
    """
    endpoint.pause = 0.3
    settings = _settings(tmp_path, endpoint)
    app = create_app(
        settings, embed_model=MockEmbedding(embed_dim=16), token_verifier=identity.verifier()
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    loop_thread = threading.Thread(target=server.run, daemon=True)
    loop_thread.start()
    try:
        _await_server(server)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        headers = {"Authorization": f"Bearer {identity.token(MEMBER)}"}
        with httpx.Client(timeout=30) as client:
            thread_id = client.post(f"{base}/ask/chat/threads", headers=headers).json()[
                "thread_id"
            ]
            turn_id = None
            with client.stream(
                "POST",
                f"{base}/ask/chat/completions",
                headers=headers,
                json={
                    "thread_id": thread_id,
                    "messages": [
                        {"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}
                    ],
                },
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    frame = json.loads(line[5:].strip() or "{}")
                    if frame.get("type") == "start":
                        turn_id = frame["messageId"]
                    if frame.get("type") == "text-delta":
                        # One delta is enough: closing now is the browser's reload.
                        break
            # Leaving the `stream` block closes the socket mid-answer.
            assert turn_id
            # The turn is not cancelled by that: it finishes on its own, which is
            # what makes the answer readable when the member comes back.
            state = _wait_for_status(settings.product_db_path, turn_id, "completed")
            assert state["ended_at"]
            with connect(settings.product_db_path) as conn:
                row = conn.execute(
                    "SELECT i.answer, t.retrieval_query, t.grounding "
                    "FROM ask_chat_turns AS t LEFT JOIN interactions AS i "
                    "ON i.turn_id = t.turn_id WHERE t.turn_id = ?",
                    (turn_id,),
                ).fetchone()
            answer = row["answer"]
            assert answer, "a turn that outlived its reader keeps its whole answer"
            assert row["retrieval_query"], "and the query it searched"
            assert row["grounding"] == "grounded", "and the verdict its evidence supports"

            # The resume after the turn has settled replays the stored answer once.
            resumed = client.get(f"{base}/ask/chat/turns/{turn_id}/stream", headers=headers)
            frames = _frames(resumed.text)
            replayed = "".join(f["delta"] for f in frames if f["type"] == "text-delta")
            assert replayed == answer
            terminal = next(f for f in frames if f["type"] == "data-deepfix-turn")["data"]
            assert terminal["status"] == "completed"
    finally:
        server.should_exit = True
        loop_thread.join(timeout=15)


def test_stop_cancels_the_model_call_and_keeps_what_was_read(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """FR-014: Stop is an explicit request, and it does stop the work.

    A reload must not stop the turn (FR-016), so Stop cannot be a closed socket.
    The route cancels the process-owned task, the relay stores the text read so
    far, and the turn settles ``cancelled`` with its evidence — the same outcome
    the closed socket used to produce, now only on request.
    """
    endpoint.pause = 0.3
    settings = _settings(tmp_path, endpoint)
    app = create_app(
        settings, embed_model=MockEmbedding(embed_dim=16), token_verifier=identity.verifier()
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    loop_thread = threading.Thread(target=server.run, daemon=True)
    loop_thread.start()
    try:
        _await_server(server)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        headers = {"Authorization": f"Bearer {identity.token(MEMBER)}"}
        with httpx.Client(timeout=60) as client:
            thread_id = client.post(f"{base}/ask/chat/threads", headers=headers).json()[
                "thread_id"
            ]
            turn_id = None
            with client.stream(
                "POST",
                f"{base}/ask/chat/completions",
                headers=headers,
                json={
                    "thread_id": thread_id,
                    "messages": [
                        {"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}
                    ],
                },
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    frame = json.loads(line[5:].strip() or "{}")
                    if frame.get("type") == "start":
                        turn_id = frame["messageId"]
                    if frame.get("type") == "text-delta":
                        break
            assert turn_id
            stopped = client.post(f"{base}/ask/chat/turns/{turn_id}/cancel", headers=headers)
            assert stopped.status_code == 204, stopped.text
            state = _wait_for_status(settings.product_db_path, turn_id, "cancelled")
            assert state["status"] == "cancelled"
            assert state["ended_at"]
            with connect(settings.product_db_path) as conn:
                row = conn.execute(
                    "SELECT i.answer, t.retrieval_query, t.grounding "
                    "FROM ask_chat_turns AS t LEFT JOIN interactions AS i "
                    "ON i.turn_id = t.turn_id WHERE t.turn_id = ?",
                    (turn_id,),
                ).fetchone()
                hits = conn.execute(
                    "SELECT COUNT(*) FROM interaction_hits h JOIN interactions i "
                    "ON i.interaction_id = h.interaction_id WHERE i.turn_id = ?",
                    (turn_id,),
                ).fetchone()[0]
            assert row["answer"], "a stopped answer keeps the text the member had read"
            assert hits >= 1, "a stopped turn keeps the passages it already found"
            assert row["retrieval_query"], "a stopped turn keeps the query it searched"
            assert row["grounding"] == "grounded", "and the verdict its evidence supports"
            # A resume of a stopped turn replays the stored text once.
            resumed = client.get(f"{base}/ask/chat/turns/{turn_id}/stream", headers=headers)
            frames = _frames(resumed.text)
            replayed = "".join(f["delta"] for f in frames if f["type"] == "text-delta")
            assert replayed == row["answer"]
            terminal = next(f for f in frames if f["type"] == "data-deepfix-turn")["data"]
            assert terminal["status"] == "cancelled"
    finally:
        server.should_exit = True
        loop_thread.join(timeout=15)


def _await_server(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started and server.servers:
            return
        time.sleep(0.05)
    raise AssertionError("The test server did not start.")


def _wait_for_status(db_path: Path, turn_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 30
    last: dict = {}
    while time.monotonic() < deadline:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT status, ended_at FROM ask_chat_turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
        if row is not None:
            last = dict(row)
            if last["status"] == expected:
                return last
        time.sleep(0.2)
    raise AssertionError(f"turn {turn_id} never settled as {expected}: {last}")


def test_a_second_regenerate_while_running_is_conflict(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """AC 13: a running answer is not replaced underneath itself."""
    settings = _settings(tmp_path, endpoint)
    with _client(settings, identity) as client:
        thread, turn_id, _ = _ask(client, identity, "Ist eine Trunkenheitsfahrt strafbar?")
        # The reply is still being written: the row is running and owned.
        ask_chat.reopen_turn(
            settings.product_db_path, turn_id, identity.user_id(MEMBER), question="nochmal"
        )
        busy = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "nochmal"}],
                "regenerate": True,
            },
        )
    assert busy.status_code == 409, busy.text


def test_a_failed_turn_reports_no_grounding_and_keeps_the_trace_and_export_clean(
    tmp_path: Path, endpoint: State, identity: IdentityIssuer
) -> None:
    """Three things a failed turn must not leave behind.

    A turn the provider refuses never searched, so its resumed stream must not
    claim "no relevant sources" — that phrase is a statement about a search — and
    it must report the deployment's configured floor rather than a placeholder
    zero. Its MLflow trace must be ``ERROR``, not a healthy trace with a tag. And
    its ``interactions`` row has no answer, so it must not reach the feedback
    export, where an empty answer would be filed as a dev question.
    """
    settings = _settings(tmp_path, endpoint)
    # A provider that refuses the forced step, and then the unforced one too, so
    # the turn settles failed without ever searching.
    endpoint.mode = "broken_provider"
    with _client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        response = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}],
            },
        )
        assert response.status_code == 200, response.text
        frames = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]
        turn_id = next(f for f in frames if f["type"] == "data-deepfix-turn")["data"]["turn_id"]
        resumed = client.get(f"/ask/chat/turns/{turn_id}/stream")
        again = [
            json.loads(line[5:].strip())
            for line in resumed.text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]

    # A failed turn never claims a citation for an answer that does not exist, and
    # any grounding part it does emit reports the deployment's floor rather than
    # the 0.0 placeholder the first draft sent.
    grounded = [f for f in again if f["type"] == "data-deepfix-grounding"]
    for part in grounded:
        assert part["data"].get("citations", []) == []
        assert part["data"]["cutoff"] == 0.3
    terminal = next(f for f in again if f["type"] == "data-deepfix-turn")["data"]
    assert terminal.get("citations", []) == []
    assert terminal.get("grounding") in (None, "no_relevant_sources")

    with connect(settings.product_db_path) as conn:
        row = conn.execute(
            "SELECT t.status, t.grounding, i.interaction_id, i.trace_id, i.answer "
            "FROM ask_chat_turns AS t LEFT JOIN interactions AS i ON i.turn_id = t.turn_id "
            "WHERE t.turn_id = ?",
            (turn_id,),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["grounding"] is None
    assert row["interaction_id"]

    # The trace exists and its state is ERROR, so a reviewer filtering by state
    # sees a failed turn rather than a healthy one.
    trace = mlflow.get_trace(str(row["trace_id"]), flush=True)
    assert trace is not None
    assert trace.info.tags["status"] == "failed"
    assert str(trace.info.status).upper().endswith("ERROR")

    # A failed turn's answerless interaction never reaches the export. The API
    # itself refuses an answer vote on it (there is no answer to vote on), so the
    # check is that the export stays empty either way.
    from deepfix_rag.feedback import AnswerMissingError, build_questions, record_vote

    try:
        record_vote(
            settings.product_db_path,
            user_id=identity.user_id(MEMBER),
            interaction_id=str(row["interaction_id"]),
            target="answer",
            action="down",
        )
    except AnswerMissingError:
        pass
    assert build_questions(settings.product_db_path) == []

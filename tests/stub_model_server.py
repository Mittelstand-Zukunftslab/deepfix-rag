"""An OpenAI-compatible stand-in for the sandbox's model endpoints.

Serves ``POST /v1/embeddings`` and ``POST /v1/chat/completions`` so a full
``deepfix index`` / ``deepfix eval`` / ``deepfix hpo`` chain can run locally
without a network. Every completion is recorded, which is how a test proves a
paraphrase was asked for once and replayed after that.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any

DIM = 64
PARAPHRASES = (
    "Wie hoch ist der Regelsatz der Regelleistung?",
    "Welche Regelsaetze gelten fuer Erwachsene?",
)


class StubModelServer:
    """A local endpoint plus the requests it received."""

    def __init__(self) -> None:
        self.completions: list[dict[str, Any]] = []
        self.embeddings = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "StubModelServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def completion_count(self) -> int:
        with self._lock:
            return len(self.completions)

    def _embedding(self, text: str) -> list[float]:
        # Deterministic and content-dependent, so different texts retrieve
        # differently and the same text always produces the same vector.
        seed = sum(ord(char) for char in text) or 1
        return [((seed * (index + 3)) % 97) / 97.0 for index in range(DIM)]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path.endswith("/embeddings"):
                    owner._answer(self, owner._embeddings_body(body))
                    return
                with owner._lock:
                    owner.completions.append(body)
                owner._answer(self, owner._chat_body(body))

            def log_message(self, *_args: object) -> None:
                return None

        return Handler

    def _embeddings_body(self, body: dict[str, Any]) -> dict[str, Any]:
        raw = body.get("input", [])
        texts = [raw] if isinstance(raw, str) else list(raw)
        with self._lock:
            self.embeddings += len(texts)
        return {
            "object": "list",
            "model": body.get("model", "stub"),
            "data": [
                {"object": "embedding", "index": index, "embedding": self._embedding(text)}
                for index, text in enumerate(texts)
            ],
            "usage": {"prompt_tokens": len(texts) * 4, "total_tokens": len(texts) * 4},
        }

    def _chat_body(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "stub"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "\n".join(PARAPHRASES)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        }

    def _answer(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

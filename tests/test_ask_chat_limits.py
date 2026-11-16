"""The Ask chat's bounds, their plumbing, and the one model.

Two kinds of case live here, and both are "meaningful integration" rather than
unit tests of a helper:

* the limits behave against the real app and a real SQLite file — the turn budget,
  the concurrent cap, the message bound, and the timeout — because a bound that is
  only declared is not a bound; and
* every ``ASK_CHAT_*`` setting the loop reads is present in ``docker-compose.yml``
  and ``example.env`` with a non-empty default, because ``backend-api`` has an
  explicit environment allowlist and no ``env_file``: a name that exists only on
  ``Settings`` silently takes its package default on the deployment, which is the
  mistake CHAT-001's plumbing test exists to catch.

The model guard is the same shape: ``Settings`` must have no ``ASK_CHAT_*MODEL``
field and ``example.env`` no such variable, so D-7 cannot be reversed quietly.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
import mlflow
import pytest

from deepfix_rag import ask_chat
from deepfix_rag.api.app import create_app
from deepfix_rag.config.settings import Settings
from deepfix_rag.storage import connect
from tests.identity import IdentityIssuer

pytestmark = [pytest.mark.api, pytest.mark.integration]

MEMBER = "ada@example.com"
OTHER = "bea@example.com"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def slow_endpoint():
    """The stalling OpenAI-compatible endpoint `tests/test_ask_chat_stop_resume.py` defines."""
    from tests.test_ask_chat_stop_resume import endpoint as stop_endpoint

    yield from stop_endpoint.__wrapped__()  # type: ignore[attr-defined]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    values: dict[str, object] = {
        "_env_file": None,
        "PRODUCT_DATA_DIR": tmp_path / "private",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "WEB_ORIGIN": "http://localhost:8080",
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        "LLM_BASE_URL": "http://127.0.0.1:9",
        "LLM_MODEL": "ask-chat-model",
        **overrides,
    }
    return Settings(**values)


def _client(settings: Settings, identity: IdentityIssuer) -> TestClient:
    return TestClient(
        create_app(
            settings,
            embed_model=MockEmbedding(embed_dim=16),
            llm=MockLLM(),
            token_verifier=identity.verifier(),
        )
    )


def _turn(settings: Settings, identity: IdentityIssuer, *, started_at: datetime | None = None):
    """Store one completed turn, optionally backdated into or out of the window."""
    owner = identity.user_id(MEMBER)
    ask_chat.init_ask_chat(settings.product_db_path)
    thread = ask_chat.create_thread(settings.product_db_path, owner)
    turn = ask_chat.append_turn(
        settings.product_db_path,
        thread.thread_id,
        owner,
        question="frage",
        status="completed",
        grounding="grounded",
    )
    if started_at is not None:
        with connect(settings.product_db_path) as conn:
            conn.execute(
                "UPDATE ask_chat_turns SET started_at = ? WHERE turn_id = ?",
                (started_at.isoformat(), turn.turn_id),
            )
    return thread, turn


def test_the_turn_budget_refuses_the_turn_after_the_limit_and_writes_no_row(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """AC 6, 7: the 21st turn in a window is 429, and a refusal is not a turn."""
    settings = _settings(
        tmp_path, ASK_CHAT_MAX_TURNS_PER_WINDOW=3, ASK_CHAT_TURN_WINDOW_SECONDS=300
    )
    owner = identity.user_id(MEMBER)
    thread, _ = _turn(settings, identity)
    for _ in range(2):
        ask_chat.append_turn(settings.product_db_path, thread.thread_id, owner, question="frage")
    with connect(settings.product_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM ask_chat_turns").fetchone()[0]

    refusal = ask_chat.check_rate_limit(
        settings.product_db_path,
        owner,
        max_turns=3,
        window_seconds=300,
        max_concurrent=99,
    )
    assert refusal is not None
    assert refusal.retry_after >= 1
    assert "3 of 3" in refusal.message
    # A refusal writes nothing, so it cannot ratchet itself into a longer ban.
    with connect(settings.product_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM ask_chat_turns").fetchone()[0]
    assert after == before

    # The window is why it was refused: the same rows, backdated, are allowed.
    old = datetime.now(timezone.utc) - timedelta(seconds=400)
    with connect(settings.product_db_path) as conn:
        conn.execute("UPDATE ask_chat_turns SET started_at = ?", (old.isoformat(),))
    assert (
        ask_chat.check_rate_limit(
            settings.product_db_path,
            owner,
            max_turns=3,
            window_seconds=300,
            max_concurrent=99,
        )
        is None
    )


def test_the_concurrent_cap_names_the_server_not_the_member(
    tmp_path: Path, identity: IdentityIssuer
) -> None:
    """AC 8: a full deployment is a "busy" 429, and it is not the member's budget."""
    settings = _settings(tmp_path, ASK_CHAT_MAX_CONCURRENT_TURNS=1)
    owner = identity.user_id(MEMBER)
    thread, _ = _turn(settings, identity)
    running = ask_chat.open_turn(
        settings.product_db_path, thread.thread_id, owner, question="läuft"
    )
    assert running.status == "running"
    refusal = ask_chat.check_rate_limit(
        settings.product_db_path,
        identity.user_id(OTHER),
        max_turns=20,
        window_seconds=300,
        max_concurrent=1,
    )
    assert refusal is not None
    assert refusal.message == ask_chat.BUSY_MESSAGE
    assert "busy" in refusal.message.lower()
    # It is a property of the deployment, so a member's own budget is untouched.
    assert (
        ask_chat.check_rate_limit(
            settings.product_db_path,
            identity.user_id(OTHER),
            max_turns=20,
            window_seconds=300,
            max_concurrent=99,
        )
        is None
    )


def test_a_long_question_and_a_full_budget_are_refused_before_any_model_call(
    tmp_path: Path, slow_endpoint, identity: IdentityIssuer
) -> None:
    """AC 6, 10: 400 naming the limit, then 429 with Retry-After, both from the app."""
    from tests.test_ask_chat_stop_resume import _client as stop_client
    from tests.test_ask_chat_stop_resume import _settings as stop_settings

    settings = stop_settings(tmp_path, slow_endpoint).model_copy(
        update={"ASK_CHAT_MAX_MESSAGE_CHARS": 20, "ASK_CHAT_MAX_TURNS_PER_WINDOW": 1}
    )
    owner = identity.user_id(MEMBER)
    with stop_client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        too_long = client.post(
            "/ask/chat/completions",
            json={"thread_id": thread, "messages": [{"role": "user", "content": "x" * 21}]},
        )
        assert too_long.status_code == 400, too_long.text
        assert "20" in too_long.json()["detail"]

        # One turn inside the window; the next is refused by the limiter, which
        # runs before any row is written, so the refusal is not itself a turn.
        ask_chat.append_turn(
            settings.product_db_path,
            thread,
            owner,
            question="frage",
            status="completed",
            grounding="grounded",
        )
        refused = client.post(
            "/ask/chat/completions",
            json={"thread_id": thread, "messages": [{"role": "user", "content": "kurz"}]},
        )
    assert refused.status_code == 429, refused.text
    assert refused.headers["retry-after"]
    assert "1 of 1" in refused.json()["detail"]


def test_every_setting_the_loop_reads_is_on_settings_compose_and_example_env() -> None:
    """AC 1, 2, 3: a name only on `Settings` is invisible on the deployment."""
    fields = {name for name in Settings.model_fields if name.startswith("ASK_CHAT_")}
    assert fields == {
        "ASK_CHAT_MAX_TURNS_PER_WINDOW",
        "ASK_CHAT_TURN_WINDOW_SECONDS",
        "ASK_CHAT_MAX_CONCURRENT_TURNS",
        "ASK_CHAT_MAX_TOOL_CALLS",
        "ASK_CHAT_MAX_HISTORY_MESSAGES",
        "ASK_CHAT_MAX_MESSAGE_CHARS",
        "ASK_CHAT_MAX_PASSAGE_CHARS",
        "ASK_CHAT_TURN_TIMEOUT_SECONDS",
        "ASK_CHAT_REASONING",
        "ASK_CHAT_MAX_OUTPUT_TOKENS",
    }
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    example = (REPO / "example.env").read_text(encoding="utf-8")
    for name in sorted(fields):
        # Compose needs a non-empty default: an empty value raises on a typed field.
        pattern = re.compile(rf"^\s+{name}:\s+\$\{{{name}:-([^}}]*)\}}", re.MULTILINE)
        found = pattern.search(compose)
        assert found is not None, f"{name} is missing from docker-compose.yml backend-api"
        assert found.group(1).strip(), f"{name} has an empty Compose default"
        assert re.search(rf"^{name}=", example, re.MULTILINE), (
            f"{name} is missing from example.env"
        )

    # The package default and the Compose default are the same number.
    for name in sorted(fields):
        declared = Settings.model_fields[name].default
        composed = re.search(rf"{name}:\s+\$\{{{name}:-([^}}]*)\}}", compose)
        assert composed is not None
        assert composed.group(1) == str(declared), f"{name} default differs from Compose"


def test_no_ask_chat_model_setting_exists_anywhere() -> None:
    """D-7: the chat resolves LLM_MODEL, and nothing may reintroduce a second model."""
    model_fields = [name for name in Settings.model_fields if re.match(r"ASK_CHAT_.*MODEL", name)]
    assert model_fields == []
    example = (REPO / "example.env").read_text(encoding="utf-8")
    variables = [
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert [name for name in variables if re.match(r"ASK_CHAT_.*MODEL", name)] == []
    # The documented fallback is a comment, not a variable, so the guard holds.
    assert "qwen/qwen3.8-27b" in example


def test_a_slow_turn_times_out_and_settles_failed_never_running(
    tmp_path: Path, slow_endpoint, identity: IdentityIssuer
) -> None:
    """AC 9: the wall clock settles the row, and the stream says why.

    The model endpoint stalls for longer than the bound, so the loop's own
    deadline ends the turn rather than the request hanging. It runs through the
    real app, so the row's terminal state is the one the route wrote.
    """
    import json
    import time

    from tests.test_ask_chat_stop_resume import _client as stop_client
    from tests.test_ask_chat_stop_resume import _settings as stop_settings

    slow_endpoint.pause = 1.5
    settings = stop_settings(tmp_path, slow_endpoint).model_copy(
        update={"ASK_CHAT_TURN_TIMEOUT_SECONDS": 1}
    )
    with stop_client(settings, identity) as client:
        identity.sign_in(client, MEMBER)
        thread = client.post("/ask/chat/threads").json()["thread_id"]
        started = time.monotonic()
        response = client.post(
            "/ask/chat/completions",
            json={
                "thread_id": thread,
                "messages": [{"role": "user", "content": "Ist eine Trunkenheitsfahrt strafbar?"}],
            },
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        frames = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}
        ]
        terminal = next(f for f in frames if f["type"] == "data-deepfix-turn")["data"]
        turn = client.get(f"/ask/chat/threads/{thread}").json()["turns"][0]
    assert terminal["status"] != "running"
    assert turn["status"] == terminal["status"]
    assert turn["ended_at"]
    # The bound is why the request returned at all, rather than the model finishing.
    assert elapsed < 30


def _coordinator(settings: Settings):
    from deepfix_rag.documents import prepare_workspace
    from deepfix_rag.indexing import IndexCoordinator

    prepare_workspace(settings.workspace_dir)
    return IndexCoordinator(
        settings.workspace_dir,
        settings.product_db_path,
        embed_model=MockEmbedding(embed_dim=16),
    )

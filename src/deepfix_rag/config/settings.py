import logging
import os
from pathlib import Path
import sys
from typing import Literal

import mlflow
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_logger():
    """Get logger for deepfix-rag.

    The CLI prints this logger to stderr. The API replaces the handler at startup
    (``deepfix_rag.api.logging_config``), so the handler is added once, not once
    per call, and a caller cannot end up printing every line twice.
    """
    logger = logging.getLogger("DeepFix RAG Eval Runner")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


class ModelNotConfigured(ValueError):
    """A model id that the caller needs is missing from the environment."""


def require_model(name: str, value: str | None = None) -> str:
    """Return a configured model id, or fail naming the environment variable.

    Every model id comes from the environment (see ``example.env`` and the
    Compose files). ``value`` overrides the ``Settings`` attribute of the same
    name; ``None`` reads it from the settings singleton.
    """
    text = (value if value is not None else getattr(settings, name, "") or "").strip()
    if not text:
        raise ModelNotConfigured(
            f"{name} is not set. Set it in the environment (see example.env) to a model id "
            "your endpoint serves."
        )
    return text


class Settings(BaseSettings):
    # MLflow
    MLFLOW_TRACKING_URI: str | None = None
    MLFLOW_DEV_RAG_EXPERIMENT: str = "dev_rag_experiment"

    DATA_ROOT: str = os.path.join(Path(__file__).parents[3], "data", "raw")
    DATA_PROCESSES_DIR: str = os.path.join(Path(__file__).parents[3], "data", "processed")

    # LLM. Every model id comes from the environment; there is no code default.
    # A consumer calls ``require_model`` and fails naming the variable when it is empty.
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = ""
    LLM_CONTEXT_WINDOW: int = 20000

    # Judge
    LLM_JUDGE_MODEL: str = ""

    # Hermes runs API. The sandbox writes the same id into its config.yaml.
    HERMES_MODEL: str = ""
    HERMES_MODEL_PROVIDER: str = "openrouter"

    # Embedding
    EMBEDDING_MODEL: str = ""
    EMBEDDING_BASE_URL: str = "https://openrouter.ai/api/v1"
    EMBEDDING_API_KEY: str | None = None

    # Reranker (OpenRouter rerank API; the model id comes from COHERE_MODEL)
    OPENROUTER_API_KEY: str | None = None
    COHERE_API_KEY: str | None = None
    COHERE_MODEL: str = ""
    COHERE_BASE_URL: str = "https://openrouter.ai/api/v1"

    # Memgraph
    MEMGRAPH_URL: str = "bolt://localhost:7687"
    MEMGRAPH_USERNAME: str = "neo4j"
    MEMGRAPH_PASSWORD: str = "neo4j"

    # S3-compatible storage
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_URI: str = "s3://deepfix-index/dev"
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_REGION: str = "us-east-1"

    # FalkorDB
    FALKORDB_HOST: str = "localhost"
    FALKORDB_PORT: int = 6379
    FALKORDB_USERNAME: str | None = None
    FALKORDB_PASSWORD: str | None = None
    FALKORDB_DATABASE: str = "my_knowledge_graph"

    # DeepFix Service
    DEEPFIX_SERVER_URL: str = "http://localhost:8844"
    DEEPFIX_HOME: str = os.path.join(Path(__file__).parents[3], ".deepfix")
    DEEPFIX_API_KEY: str | None = None

    # Product API
    WEB_ORIGIN: str = "http://localhost:8080"
    # Console log level for the API process: the ``deepfix_rag`` and ``apscheduler``
    # loggers print to stdout at this level. An unknown value falls back to INFO
    # with a warning; it never stops the API from starting.
    DEEPFIX_LOG_LEVEL: str = "INFO"
    PRODUCT_DATA_DIR: Path = Path.home() / ".deepfix" / "product"
    WORKSPACE_DIR: Path = Path.home() / ".deepfix" / "workspace"
    # Identity tokens come from Better Auth in the web app. Issuer and audience default to
    # WEB_ORIGIN, which must match BETTER_AUTH_URL there.
    AUTH_JWKS_URL: str | None = None
    AUTH_ISSUER: str | None = None
    AUTH_AUDIENCE: str | None = None
    MAX_DISPLAYED_HITS: int = 10
    # The Ask chat's bounds (D-8), documented and plumbed in example.env and the
    # backend-api Compose environment. Members outnumber admins and share one API
    # process, so these are smaller than the admin chat's.
    #
    # Turns one member may start inside the window; the next is a 429.
    ASK_CHAT_MAX_TURNS_PER_WINDOW: int = 20
    # The window itself, in seconds, so operators learn one number.
    ASK_CHAT_TURN_WINDOW_SECONDS: int = 300
    # Turns running across the whole deployment at once.
    ASK_CHAT_MAX_CONCURRENT_TURNS: int = 4
    # Tool calls one turn may make; the next model step is forced to prose.
    ASK_CHAT_MAX_TOOL_CALLS: int = 3
    # Stored messages from earlier turns sent as context.
    ASK_CHAT_MAX_HISTORY_MESSAGES: int = 12
    # Longest question accepted; a longer one is a 400 naming this limit.
    ASK_CHAT_MAX_MESSAGE_CHARS: int = 4000
    # One passage as sent to the model. The stored hit keeps the full text.
    ASK_CHAT_MAX_PASSAGE_CHARS: int = 1200
    # Wall clock one answer may take before the turn is stopped and failed.
    ASK_CHAT_TURN_TIMEOUT_SECONDS: int = 120
    # How much the model may think before it answers (D-12). A reasoning model at
    # its provider default spends the whole output budget thinking and emits no
    # prose until that budget is gone — qwen3.8-flash did exactly that for 93 s on
    # one turn — so this is a latency control, not a quality knob. "off" sends the
    # provider's own disable flag; the other values pass the effort level through.
    # It is a setting because a deployment may genuinely want the reasoning.
    #
    # This is the level for the **answer** step. A step that may call a tool uses
    # ASK_CHAT_TOOL_STEP_REASONING instead: with thinking off, the model was
    # observed opening with a refusal or role prose before any tool call in 4 of 7
    # live turns, so the searching steps keep a little room to reason (D-12).
    ASK_CHAT_REASONING: Literal["off", "low", "medium", "high"] = "off"
    # How much a step that may call `search_documents` may think. `low` is the
    # measured setting: six of six live first steps called the tool cleanly with no
    # prose first, in 1.1–2.2 s. Off is not the default here because it makes the
    # model answer before it looks.
    ASK_CHAT_TOOL_STEP_REASONING: Literal["off", "low", "medium", "high"] = "low"
    # Most tokens one step may produce. Reasoning tokens count against it, which is
    # why an uncapped reasoning step produced no visible text at all: the whole
    # budget went to the hidden part. Bounded so a runaway step fails fast instead
    # of running to the turn's wall clock.
    ASK_CHAT_MAX_OUTPUT_TOKENS: int = 2048
    # Omit sampling params the provider rejects. Claude Haiku 5.5 allows only
    # temperature=1. Read by llm.py for every LiteLLM completion, not only the
    # optimization agent, so it stays after OpenHands was removed.
    DEEPFIX_LLM_DROP_PARAMS: bool = True
    # Hermes is the only optimization agent. The key stays so a stale
    # OPTIMIZATION_AGENT=openhands in a server env file fails at startup naming
    # the variable rather than being silently ignored.
    OPTIMIZATION_AGENT: Literal["hermes"] = "hermes"
    HERMES_BASE_URL: str = "http://hermes:8642"
    # Bearer token for the Hermes API. Empty leaves the hermes agent unavailable.
    API_SERVER_KEY: str | None = None

    # Admin Hermes chat. Off by default: nothing listens on 8643 and the chat
    # profile's gateway is not started. The audit read and thread delete stay
    # reachable either way (D-4).
    HERMES_CHAT_ENABLED: bool = False
    HERMES_CHAT_BASE_URL: str = "http://hermes:8643"
    # Bearer for the chat profile only. Never the optimization key.
    HERMES_CHAT_API_SERVER_KEY: str | None = None
    # Model the chat profile runs. Empty falls back to HERMES_MODEL (D-3).
    HERMES_CHAT_MODEL: str = ""
    MLFLOW_HERMES_CHAT_EXPERIMENT: str = "deepfix_hermes_chat"
    ADMIN_CHAT_MAX_PROMPT_CHARS: int = 48000
    ADMIN_CHAT_MAX_MESSAGES: int = 60
    ADMIN_CHAT_TURN_TIMEOUT_SECONDS: float = 300
    ADMIN_CHAT_DIGEST_MAX_CHARS: int = 6000
    ADMIN_CHAT_MAX_TURNS_PER_WINDOW: int = 30
    ADMIN_CHAT_TURN_WINDOW_SECONDS: int = 300
    ADMIN_CHAT_MAX_CONCURRENT_TURNS: int = 2
    ADMIN_CHAT_ACTION_TTL_SECONDS: int = 600
    ADMIN_CHAT_STEP_TAIL: int = 50
    ADMIN_CHAT_STEP_READ_BYTES: int = 65536
    # Audit rows are kept this long and pruned hourly. 0 or a negative value
    # keeps them forever (D-6).
    ADMIN_CHAT_AUDIT_RETENTION_DAYS: int = 14
    OPTIMIZATION_MIN_DEV_QUESTIONS: int = 50
    OPTIMIZATION_MIN_NEW_DEV_QUESTIONS: int = 10
    OPTIMIZATION_MIN_HOLDOUT_QUESTIONS: int = 15
    OPTIMIZATION_MIN_PRIMARY_DELTA: float = 0.025
    OPTIMIZATION_SECONDARY_TOLERANCE: float = -0.01
    OPTIMIZATION_RUN_TIMEOUT_HOURS: float = 3
    # No non-keepalive Hermes event and no fresh output/heartbeat.json for this
    # many minutes fails the row. SSE ": keepalive" comments do not count.
    OPTIMIZATION_HEARTBEAT_STALL_MINUTES: float = 5
    # Seconds for `deepfix index`. 0 disables the workflow timeout. The class
    # default of 600 killed a 2,000-document index. Query workflows stay at 600.
    DEEPFIX_INDEX_TIMEOUT_SECONDS: float = 0

    @property
    def product_db_path(self) -> Path:
        """SQLite file for documents, interactions, and runs, outside the agent workspace."""
        return Path(self.PRODUCT_DATA_DIR) / "product.db"

    @property
    def workspace_dir(self) -> Path:
        """Shared corpus, indexes, feedback, configs, and runs."""
        return Path(self.WORKSPACE_DIR)

    @property
    def auth_jwks_url(self) -> str:
        """Where the web app publishes the public keys that sign identity tokens."""
        return self.AUTH_JWKS_URL or f"{self.WEB_ORIGIN.rstrip('/')}/api/auth/jwks"

    @property
    def auth_issuer(self) -> str:
        """Expected ``iss`` claim on identity tokens."""
        return self.AUTH_ISSUER or self.WEB_ORIGIN.rstrip("/")

    @property
    def auth_audience(self) -> str:
        """Expected ``aud`` claim on identity tokens."""
        return self.AUTH_AUDIENCE or self.WEB_ORIGIN.rstrip("/")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

LOGGER = get_logger()

if settings.MLFLOW_TRACKING_URI:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

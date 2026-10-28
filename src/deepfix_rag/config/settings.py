import logging
import os
from pathlib import Path
import sys

import mlflow
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_logger():
    """Get logger for deepfix-rag."""
    logger = logging.getLogger("DeepFix RAG Eval Runner")
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


class Settings(BaseSettings):
    # MLflow
    MLFLOW_TRACKING_URI: str | None = None
    MLFLOW_DEV_RAG_EXPERIMENT: str = "dev_rag_experiment"

    DATA_ROOT: str = os.path.join(Path(__file__).parents[3], "data", "raw")
    DATA_PROCESSES_DIR: str = os.path.join(Path(__file__).parents[3], "data", "processed")

    # LLM
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "anthropic/claude-haiku-5.5"
    LLM_CONTEXT_WINDOW: int = 20000

    # Judge
    LLM_JUDGE_MODEL: str = "openai/gpt-5.6-luna"

    # Embedding
    EMBEDDING_MODEL: str = "perplexity/pplx-embed-v1-4b"
    EMBEDDING_BASE_URL: str = "https://openrouter.ai/api/v1"
    EMBEDDING_API_KEY: str | None = None

    # Reranker (Cohere-compatible)
    COHERE_API_KEY: str | None = None
    COHERE_MODEL: str = "cohere/rerank-4-fast"
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
    PRODUCT_DATA_DIR: Path = Path.home() / ".deepfix" / "product"
    WORKSPACE_DIR: Path = Path.home() / ".deepfix" / "workspace"
    OH_SESSION_HOST: str | None = None
    # Identity tokens come from Better Auth in the web app. Issuer and audience default to
    # WEB_ORIGIN, which must match BETTER_AUTH_URL there.
    AUTH_JWKS_URL: str | None = None
    AUTH_ISSUER: str | None = None
    AUTH_AUDIENCE: str | None = None
    MAX_DISPLAYED_HITS: int = 10
    # OpenRouter credentials for the OpenHands child. The key is never the product LLM key.
    DEEPFIX_LLM_API_KEY: str | None = None
    DEEPFIX_LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEEPFIX_LLM_MODEL_NAME: str = "openai/gpt-6-luna-pro"
    OPENHANDS_PYTHON: str | None = None
    OPTIMIZATION_MIN_DEV_QUESTIONS: int = 50
    OPTIMIZATION_MIN_NEW_DEV_QUESTIONS: int = 10
    OPTIMIZATION_MIN_HOLDOUT_QUESTIONS: int = 15
    OPTIMIZATION_MIN_PRIMARY_DELTA: float = 0.025
    OPTIMIZATION_RUN_TIMEOUT_HOURS: float = 3
    OPTIMIZATION_GOAL_ROUNDS: int = 3

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

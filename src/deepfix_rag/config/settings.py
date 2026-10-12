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
    LLM_BASE_URL: str = "https://api.aisc.hpi.de/v1"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "qwen3-8-27b"
    LLM_CONTEXT_WINDOW: int = 20000

    # Judge
    LLM_JUDGE_MODEL: str = "gpt-oss-120b"

    # Embedding
    EMBEDDING_MODEL: str = "octen-embedding-8b"
    EMBEDDING_BASE_URL: str = "https://api.aisc.hpi.de/v1"
    EMBEDDING_API_KEY: str | None = None

    # Reranker (Cohere-compatible)
    COHERE_API_KEY: str | None = None
    COHERE_MODEL: str = "qwen3-reranker-4b"
    COHERE_BASE_URL: str = "https://api.aisc.hpi.de"

    # Memgraph
    MEMGRAPH_URL: str = "bolt://localhost:7687"
    MEMGRAPH_USERNAME: str = "neo4j"
    MEMGRAPH_PASSWORD: str = "neo4j"

    # S3-compatible storage
    S3_ENDPOINT_URL: str = "https://hpi-s3.mittelstand-zukunftslab.de"
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
    ALLOWED_EMAIL_DOMAINS: str = ""
    MAX_DISPLAYED_HITS: int = 10
    # OpenRouter credentials for the Hermes child. The key is never the product LLM key.
    DEEPFIX_LLM_API_KEY: str | None = None
    DEEPFIX_LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEEPFIX_LLM_MODEL_NAME: str = "openai/gpt-6-luna-pro"
    HERMES_PYTHON: str | None = None
    OPTIMIZATION_MIN_DEV_QUESTIONS: int = 50
    OPTIMIZATION_MIN_NEW_DEV_QUESTIONS: int = 10
    OPTIMIZATION_MIN_HOLDOUT_QUESTIONS: int = 15
    OPTIMIZATION_MIN_PRIMARY_DELTA: float = 0.025
    OPTIMIZATION_RUN_TIMEOUT_HOURS: float = 3

    @property
    def product_db_path(self) -> Path:
        """SQLite file for users, sessions, and documents, outside the agent workspace."""
        return Path(self.PRODUCT_DATA_DIR) / "product.db"

    @property
    def workspace_dir(self) -> Path:
        """Shared corpus, indexes, feedback, configs, and runs."""
        return Path(self.WORKSPACE_DIR)

    @property
    def allowed_email_domains(self) -> list[str]:
        """Domains that may register, or an empty list when registration is open."""
        return [
            domain.strip().lower()
            for domain in self.ALLOWED_EMAIL_DOMAINS.split(",")
            if domain.strip()
        ]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

LOGGER = get_logger()

if settings.MLFLOW_TRACKING_URI:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

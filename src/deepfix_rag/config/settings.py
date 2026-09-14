import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # MLflow
    MLFLOW_TRACKING_USERNAME: str | None = None
    MLFLOW_TRACKING_PASSWORD: str | None = None
    MLFLOW_TRACKING_URI: str | None = None
    MLFLOW_DEV_WORKSPACE: str | None = None
    MLFLOW_CANDIDATE_ID: str = "baseline_001"
    MLFLOW_DEV_RAG_EXPERIMENT: str = "dev_rag_experiment"
    MLFLOW_CORPUS_ID: str | None = None

    # LLM
    LLM_BASE_URL: str = "https://api.aisc.hpi.de/v1"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "qwen3-8-27b"

    # Embedding
    EMBEDDING_MODEL: str = "octen-embedding-8b"
    EMBEDDING_BASE_URL: str = "https://api.aisc.hpi.de/v1"
    EMBEDDING_API_KEY: str | None = None

    # Reranker (Cohere-compatible)
    COHERE_API_KEY: str | None = None
    COHERE_MODEL: str = "qwen3-reranker-4b"
    COHERE_BASE_URL: str = "https://api.aisc.hpi.de/v1"

    # Memgraph
    MEMGRAPH_URL: str = "bolt://localhost:7687"
    MEMGRAPH_USERNAME: str = "neo4j"
    MEMGRAPH_PASSWORD: str = "neo4j"

    # FalkorDB
    FALKORDB_HOST: str = "localhost"
    FALKORDB_PORT: int = 6379
    FALKORDB_USERNAME: str | None = None
    FALKORDB_PASSWORD: str | None = None
    FALKORDB_DATABASE: str = "my_knowledge_graph"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # MLflow
    MLFLOW_RAG_USERNAME: Optional[str] = None
    MLFLOW_RAG_PASSWORD: Optional[str] = None
    MLFLOW_TRACKING_URI: Optional[str] = None
    MLFLOW_DEV_WORKSPACE: Optional[str] = None
    MLFLOW_CANDIDATE_ID: str = "baseline_001"
    MLFLOW_DEV_RAG_EXPERIMENT: str = "dev_rag_experiment"
    MLFLOW_CORPUS_ID: Optional[str] = None

    # Embeddings
    EMBEDDING_PROVIDER: str = "aisc"
    EMBEDDING_MODEL: str = "octen-embedding-8b"
    
    # LiteLLM
    LITELLM_URL: str = "https://litellm.mittelstand-zukunftslab.de"
    LITELLM_KEY: Optional[str] = None
    LITELLM_TELEMETRY: str = "False"

    # Octen / AISC cluster
    OCTEN_URL: str = "https://api.aisc.hpi.de/v1"
    OCTEN_KEY: Optional[str] = None

    # OpenRouter
    OPENROUTER_KEY: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def apply_env_vars(self):
        """Apply specific variables back to os.environ so third-party libraries (like mlflow, litellm) can pick them up automatically."""
        if self.MLFLOW_RAG_USERNAME:
            os.environ["MLFLOW_TRACKING_USERNAME"] = self.MLFLOW_RAG_USERNAME
        if self.MLFLOW_RAG_PASSWORD:
            os.environ["MLFLOW_TRACKING_PASSWORD"] = self.MLFLOW_RAG_PASSWORD
        if self.MLFLOW_TRACKING_URI:
            os.environ["MLFLOW_TRACKING_URI"] = self.MLFLOW_TRACKING_URI
        if self.LITELLM_TELEMETRY is not None:
            os.environ["LITELLM_TELEMETRY"] = self.LITELLM_TELEMETRY

settings = Settings()
settings.apply_env_vars()

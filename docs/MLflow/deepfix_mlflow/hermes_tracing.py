from __future__ import annotations

import base64
import os
from dataclasses import dataclass


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


@dataclass(frozen=True)
class HermesMLflowTracingConfig:
    endpoint: str
    workspace: str
    experiment_id: str
    authorization_header: str

    @classmethod
    def from_env(cls, *, experiment_id: str) -> "HermesMLflowTracingConfig":
        tracking_uri = _require_env("MLFLOW_TRACKING_URI").rstrip("/")
        username = _require_env("MLFLOW_HERMES_SERVICE_USERNAME")
        password = _require_env("MLFLOW_HERMES_SERVICE_PASSWORD")
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        return cls(
            endpoint=f"{tracking_uri}/v1/traces",
            workspace=_require_env("MLFLOW_DEV_WORKSPACE"),
            experiment_id=experiment_id,
            authorization_header=f"Basic {token}",
        )

    def hermes_otel_backend_yaml(self) -> str:
        """Return a config fragment that keeps the Basic token in an env var."""
        return (
            "enabled: true\n"
            "force_flush_on_session_end: true\n"
            "backends:\n"
            "  - type: otlp\n"
            "    name: mlflow\n"
            f"    endpoint: {self.endpoint}\n"
            "    metrics: false\n"
            "    logs: false\n"
            "    headers:\n"
            f"      X-MLFLOW-WORKSPACE: {self.workspace}\n"
            f"      x-mlflow-experiment-id: {self.experiment_id}\n"
            "      Authorization: ${MLFLOW_HERMES_OTLP_AUTHORIZATION}\n"
        )

    def exporter_environment(self) -> dict[str, str]:
        return {"MLFLOW_HERMES_OTLP_AUTHORIZATION": self.authorization_header}

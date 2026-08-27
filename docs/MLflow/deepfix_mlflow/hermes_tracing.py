from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class HermesMLflowTracingError(RuntimeError):
    """Raised when Hermes MLflow tracing configuration cannot be resolved."""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise HermesMLflowTracingError(
            f"Missing required environment variable: {name}"
        )
    return value.strip()


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return f"Basic {token}"


def _resolve_experiment_id(
    *,
    tracking_uri: str,
    workspace: str,
    experiment_name: str,
    authorization_header: str,
    timeout_seconds: float,
) -> str:
    """
    Resolve an existing MLflow experiment name to its experiment ID.

    Uses the Hermes tracing/service identity and the configured workspace.
    This is read-only discovery: the function never creates or modifies
    experiments.
    """
    query = urllib.parse.urlencode({"experiment_name": experiment_name})
    url = (
        f"{tracking_uri.rstrip('/')}"
        f"/api/2.0/mlflow/experiments/get-by-name?{query}"
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": authorization_header,
            "X-MLFLOW-WORKSPACE": workspace,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HermesMLflowTracingError(
            "Failed to resolve Hermes MLflow experiment "
            f"{experiment_name!r} in workspace {workspace!r}: "
            f"HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise HermesMLflowTracingError(
            "Failed to connect to MLflow while resolving Hermes experiment "
            f"{experiment_name!r}: {exc}"
        ) from exc

    try:
        data: dict[str, Any] = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HermesMLflowTracingError(
            "MLflow returned invalid JSON while resolving "
            f"Hermes experiment {experiment_name!r}"
        ) from exc

    experiment = data.get("experiment")
    if not isinstance(experiment, dict):
        raise HermesMLflowTracingError(
            "Configured Hermes MLflow experiment does not exist or is not "
            f"accessible: {experiment_name!r}"
        )

    experiment_id = experiment.get("experiment_id")
    if experiment_id is None or not str(experiment_id).strip():
        raise HermesMLflowTracingError(
            "MLflow returned the Hermes experiment without an experiment_id: "
            f"{experiment_name!r}"
        )

    return str(experiment_id).strip()


@dataclass(frozen=True)
class HermesMLflowTracingConfig:
    """
    Fully resolved Hermes -> hermes-otel -> MLflow tracing configuration.

    The caller supplies no experiment ID. `from_env()` resolves the configured
    Hermes experiment by name using the dedicated Hermes service identity.

    Required environment variables:
        MLFLOW_TRACKING_URI
        MLFLOW_DEV_WORKSPACE
        MLFLOW_DEV_HERMES_EXPERIMENT
        MLFLOW_HERMES_SERVICE_USERNAME
        MLFLOW_HERMES_SERVICE_PASSWORD
    """

    tracking_uri: str
    endpoint: str
    workspace: str
    experiment_name: str
    experiment_id: str
    authorization_header: str

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: float = 15.0,
    ) -> "HermesMLflowTracingConfig":
        """
        Resolve the complete tracing configuration from environment variables.

        Target behavior:
            HermesMLflowTracingConfig.from_env()

        No manual experiment ID is accepted or required.

        The experiment named by MLFLOW_DEV_HERMES_EXPERIMENT must already
        exist. Failure to resolve it is treated as a deployment/configuration
        error rather than silently creating a replacement.
        """
        tracking_uri = _require_env("MLFLOW_TRACKING_URI").rstrip("/")
        workspace = _require_env("MLFLOW_DEV_WORKSPACE")
        experiment_name = _require_env("MLFLOW_DEV_HERMES_EXPERIMENT")
        username = _require_env("MLFLOW_HERMES_SERVICE_USERNAME")
        password = _require_env("MLFLOW_HERMES_SERVICE_PASSWORD")

        authorization_header = _basic_auth_header(username, password)

        experiment_id = _resolve_experiment_id(
            tracking_uri=tracking_uri,
            workspace=workspace,
            experiment_name=experiment_name,
            authorization_header=authorization_header,
            timeout_seconds=timeout_seconds,
        )

        return cls(
            tracking_uri=tracking_uri,
            endpoint=f"{tracking_uri}/v1/traces",
            workspace=workspace,
            experiment_name=experiment_name,
            experiment_id=experiment_id,
            authorization_header=authorization_header,
        )

    def hermes_otel_backend_yaml(self) -> str:
        """
        Render the hermes-otel OTLP backend configuration.

        The Basic Auth credential is not embedded in the YAML. The generated
        configuration references MLFLOW_HERMES_OTLP_AUTHORIZATION instead.
        """
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
        """
        Return deployment-time environment variables required by hermes-otel.

        This mapping contains a credential-derived Authorization value and
        should not be committed to source control.
        """
        return {
            "MLFLOW_HERMES_OTLP_AUTHORIZATION": self.authorization_header,
        }

    def redacted_summary(self) -> dict[str, str]:
        """Return resolved routing information that is safe to log."""
        return {
            "tracking_uri": self.tracking_uri,
            "endpoint": self.endpoint,
            "workspace": self.workspace,
            "experiment_name": self.experiment_name,
            "experiment_id": self.experiment_id,
        }

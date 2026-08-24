from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


class MLflowReadError(RuntimeError):
    """Raised when the read-only MLflow client cannot complete a request."""


@dataclass(frozen=True)
class MLflowReadConfig:
    tracking_uri: str
    workspace: str
    experiment_name: str
    username: str
    password: str
    timeout_seconds: float = 15.0

    @classmethod
    def for_hermes_from_env(cls) -> "MLflowReadConfig":
        return cls(
            tracking_uri=_require_env("MLFLOW_TRACKING_URI"),
            workspace=_require_env("MLFLOW_DEV_WORKSPACE"),
            experiment_name=_require_env("MLFLOW_DEV_RAG_EXPERIMENT"),
            username=_require_env("MLFLOW_HERMES_READER_USERNAME"),
            password=_require_env("MLFLOW_HERMES_READER_PASSWORD"),
        )

    @classmethod
    def for_deepfix_from_env(cls) -> "MLflowReadConfig":
        return cls(
            tracking_uri=_require_env("MLFLOW_TRACKING_URI"),
            workspace=_require_env("MLFLOW_DEV_WORKSPACE"),
            experiment_name=_require_env("MLFLOW_DEV_RAG_EXPERIMENT"),
            username=_require_env("MLFLOW_DEEPFIX_USERNAME"),
            password=_require_env("MLFLOW_DEEPFIX_PASSWORD"),
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


class MLflowEvidenceReader:
    """Read-only wrapper around the project-approved MLflow HTTP APIs.

    MLflow search operations may use HTTP POST, so read-only is enforced by
    the exposed capabilities and server RBAC, not by allowing only GET.
    """

    def __init__(self, config: MLflowReadConfig):
        self.config = config
        self._base = config.tracking_uri.rstrip("/")
        token = base64.b64encode(
            f"{config.username}:{config.password}".encode("utf-8")
        ).decode("ascii")
        self._headers = {
            "Authorization": f"Basic {token}",
            "X-MLFLOW-WORKSPACE": config.workspace,
            "Accept": "application/json",
        }
        self._experiment_id: str | None = None

    @classmethod
    def for_hermes_from_env(cls) -> "MLflowEvidenceReader":
        return cls(MLflowReadConfig.for_hermes_from_env())

    @classmethod
    def for_deepfix_from_env(cls) -> "MLflowEvidenceReader":
        return cls(MLflowReadConfig.for_deepfix_from_env())

    def _request(self, method: str, path: str, *, query: Mapping[str, Any] | None = None,
                 body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if query:
            encoded = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}, doseq=True
            )
            url = f"{url}?{encoded}"
        data = None
        headers = dict(self._headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MLflowReadError(
                f"MLflow request failed: {method} {path} -> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise MLflowReadError(f"MLflow request failed: {method} {path}: {exc}") from exc
        return {} if not payload else json.loads(payload.decode("utf-8"))

    def get_target_experiment(self) -> dict[str, Any]:
        result = self._request(
            "GET", "/api/2.0/mlflow/experiments/get-by-name",
            query={"experiment_name": self.config.experiment_name},
        )
        experiment = result.get("experiment")
        if not experiment:
            raise MLflowReadError(
                f"Configured experiment not found: {self.config.experiment_name}"
            )
        self._experiment_id = str(experiment["experiment_id"])
        return experiment

    @property
    def experiment_id(self) -> str:
        if self._experiment_id is None:
            self.get_target_experiment()
        assert self._experiment_id is not None
        return self._experiment_id

    def search_runs(self, *, filter_string: str | None = None,
                    max_results: int = 100, order_by: list[str] | None = None,
                    page_token: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "experiment_ids": [self.experiment_id],
            "max_results": max_results,
        }
        if filter_string:
            body["filter"] = filter_string
        if order_by:
            body["order_by"] = order_by
        if page_token:
            body["page_token"] = page_token
        return self._request("POST", "/api/2.0/mlflow/runs/search", body=body)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET", "/api/2.0/mlflow/runs/get", query={"run_id": run_id}
        )

    def search_traces(self, *, filter_string: str | None = None,
                      max_results: int = 100, order_by: list[str] | None = None,
                      page_token: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "locations": [{
                "type": "MLFLOW_EXPERIMENT",
                "mlflow_experiment": {"experiment_id": self.experiment_id},
            }],
            "max_results": max_results,
        }
        if filter_string:
            body["filter"] = filter_string
        if order_by:
            body["order_by"] = order_by
        if page_token:
            body["page_token"] = page_token
        return self._request("POST", "/api/3.0/mlflow/traces/search", body=body)

    def get_trace_info(self, trace_id: str) -> dict[str, Any]:
        safe = urllib.parse.quote(trace_id, safe="")
        return self._request("GET", f"/api/3.0/mlflow/traces/{safe}")

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        return self._request(
            "GET", "/api/3.0/mlflow/traces/get", query={"trace_id": trace_id}
        )

    def search_logged_models(self, *, filter_string: str | None = None,
                             max_results: int = 100,
                             page_token: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "experiment_ids": [self.experiment_id],
            "max_results": max_results,
        }
        if filter_string:
            body["filter"] = filter_string
        if page_token:
            body["page_token"] = page_token
        return self._request("POST", "/api/2.0/mlflow/logged-models/search", body=body)

    def get_logged_model(self, model_id: str) -> dict[str, Any]:
        safe = urllib.parse.quote(model_id, safe="")
        return self._request("GET", f"/api/2.0/mlflow/logged-models/{safe}")

    def list_logged_model_artifacts(self, model_id: str,
                                    *, artifact_path: str | None = None) -> dict[str, Any]:
        safe = urllib.parse.quote(model_id, safe="")
        query = {"artifact_path": artifact_path} if artifact_path else None
        return self._request(
            "GET",
            f"/api/2.0/mlflow/logged-models/{safe}/artifacts/directories",
            query=query,
        )

    def find_candidate(self, candidate_id: str) -> dict[str, Any]:
        result = self.search_logged_models(filter_string=f"name = '{candidate_id}'")
        models = result.get("models") or []
        if len(models) != 1:
            raise MLflowReadError(
                f"Expected exactly one LoggedModel named {candidate_id!r}; found {len(models)}"
            )
        return models[0]

    def get_evaluation_runs(self, candidate_id: str) -> dict[str, Any]:
        escaped = candidate_id.replace("'", "\\'")
        return self.search_runs(filter_string=f"params.candidate_id = '{escaped}'")

    def search_candidate_traces(self, candidate_id: str,
                                *, max_results: int = 100) -> dict[str, Any]:
        escaped = candidate_id.replace("'", "\\'")
        return self.search_traces(
            filter_string=f"request_metadata.`candidate_id` = '{escaped}'",
            max_results=max_results,
        )

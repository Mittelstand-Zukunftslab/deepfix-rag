from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping


class MLflowReadError(RuntimeError):
    """Raised when the read-only MLflow evidence client cannot complete a request."""


_CANDIDATE_ID_PATTERN = re.compile(r"^(?:baseline|candidate)_\d{3,}$")
_SAFE_FILTER_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_REQUIRED_MANIFEST_KEYS = {
    "schema_version",
    "candidate_id",
    "config_hash",
    "rag_type",
    "configuration",
}


@dataclass(frozen=True)
class MLflowReadConfig:
    tracking_uri: str
    workspace: str
    experiment_name: str
    username: str
    password: str = field(repr=False)
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


@dataclass(frozen=True)
class Candidate:
    """Project-level view of one immutable RAG candidate LoggedModel."""

    candidate_id: str
    model_id: str
    experiment_id: str
    artifact_uri: str
    rag_type: str | None
    config_hash: str | None
    status: str | None
    creation_timestamp_ms: int | None
    last_updated_timestamp_ms: int | None
    params: dict[str, str]
    tags: dict[str, str]
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class EvaluationSummary:
    """Project-level view of one MLflow evaluation run."""

    run_id: str
    experiment_id: str | None
    candidate_id: str | None
    corpus_id: str | None
    evaluator_version: str | None
    status: str | None
    start_time_ms: int | None
    end_time_ms: int | None
    metrics: dict[str, float]
    params: dict[str, str]
    tags: dict[str, str]
    raw: dict[str, Any] = field(repr=False)

    @property
    def duration_ms(self) -> int | None:
        if self.start_time_ms is None or self.end_time_ms is None:
            return None
        return self.end_time_ms - self.start_time_ms


@dataclass(frozen=True)
class EvaluationPage:
    evaluations: tuple[EvaluationSummary, ...]
    next_page_token: str | None = None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _validate_candidate_id(candidate_id: str) -> str:
    candidate_id = candidate_id.strip()
    if not _CANDIDATE_ID_PATTERN.fullmatch(candidate_id):
        raise ValueError(
            "candidate_id must match baseline_NNN or candidate_NNN "
            "(three or more digits)"
        )
    return candidate_id


def _safe_filter_value(value: str, *, field_name: str) -> str:
    """
    Validate identifiers inserted into MLflow filter strings.

    Project identifiers are deliberately constrained rather than relying on
    SQL-like escaping rules that vary across MLflow filter grammars.
    """
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if not _SAFE_FILTER_VALUE_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} contains unsupported characters: {value!r}"
        )
    return value


def _kv_list_to_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    if not isinstance(value, list):
        return {}

    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if key is None:
            continue
        raw_value = item.get("value")
        result[str(key)] = "" if raw_value is None else str(raw_value)
    return result


def _metrics_to_dict(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        result: dict[str, float] = {}
        for key, raw_value in value.items():
            try:
                result[str(key)] = float(raw_value)
            except (TypeError, ValueError):
                continue
        return result
    if not isinstance(value, list):
        return {}

    result: dict[str, float] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        raw_value = item.get("value")
        if key is None or raw_value is None:
            continue
        try:
            result[str(key)] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return result


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unwrap_logged_model(raw: Mapping[str, Any]) -> dict[str, Any]:
    model = raw.get("model")
    if isinstance(model, Mapping):
        return dict(model)
    return dict(raw)


def _candidate_from_raw(raw: Mapping[str, Any]) -> Candidate:
    model = _unwrap_logged_model(raw)
    info_raw = model.get("info")
    data_raw = model.get("data")
    info = dict(info_raw) if isinstance(info_raw, Mapping) else model
    data = dict(data_raw) if isinstance(data_raw, Mapping) else model

    params = _kv_list_to_dict(data.get("params") or model.get("params"))
    tags = _kv_list_to_dict(info.get("tags") or model.get("tags"))

    model_id = info.get("model_id") or model.get("model_id")
    name = info.get("name") or model.get("name")
    experiment_id = info.get("experiment_id") or model.get("experiment_id")
    artifact_uri = (
        info.get("artifact_uri")
        or info.get("artifact_location")
        or model.get("artifact_uri")
        or model.get("artifact_location")
    )

    missing = [
        key
        for key, value in (
            ("model_id", model_id),
            ("name", name),
            ("experiment_id", experiment_id),
            ("artifact_uri", artifact_uri),
        )
        if value is None or str(value).strip() == ""
    ]
    if missing:
        raise MLflowReadError(
            "LoggedModel response is missing required field(s): "
            + ", ".join(missing)
        )

    return Candidate(
        candidate_id=str(name),
        model_id=str(model_id),
        experiment_id=str(experiment_id),
        artifact_uri=str(artifact_uri),
        rag_type=params.get("rag_type"),
        config_hash=params.get("config_hash"),
        status=(
            str(info.get("status"))
            if info.get("status") is not None
            else None
        ),
        creation_timestamp_ms=_as_int(
            info.get("creation_timestamp_ms")
            or info.get("creation_timestamp")
        ),
        last_updated_timestamp_ms=_as_int(
            info.get("last_updated_timestamp_ms")
            or info.get("last_updated_timestamp")
        ),
        params=params,
        tags=tags,
        raw=model,
    )


def _unwrap_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    run = raw.get("run")
    if isinstance(run, Mapping):
        return dict(run)
    return dict(raw)


def _evaluation_from_raw(raw: Mapping[str, Any]) -> EvaluationSummary:
    run = _unwrap_run(raw)
    info_raw = run.get("info")
    data_raw = run.get("data")
    info = dict(info_raw) if isinstance(info_raw, Mapping) else {}
    data = dict(data_raw) if isinstance(data_raw, Mapping) else {}

    params = _kv_list_to_dict(data.get("params"))
    tags = _kv_list_to_dict(data.get("tags"))
    metrics = _metrics_to_dict(data.get("metrics"))

    run_id = (
        info.get("run_id")
        or info.get("run_uuid")
        or info.get("runUuid")
        or run.get("run_id")
    )
    if run_id is None:
        raise MLflowReadError("Run response does not contain a run_id")

    return EvaluationSummary(
        run_id=str(run_id),
        experiment_id=(
            str(
                info.get("experiment_id")
                or info.get("experimentId")
            )
            if (
                info.get("experiment_id") is not None
                or info.get("experimentId") is not None
            )
            else None
        ),
        candidate_id=params.get("candidate_id"),
        corpus_id=params.get("corpus_id"),
        evaluator_version=params.get("evaluator_version"),
        status=(
            str(info.get("status"))
            if info.get("status") is not None
            else None
        ),
        start_time_ms=_as_int(
            info.get("start_time")
            or info.get("startTime")
        ),
        end_time_ms=_as_int(
            info.get("end_time")
            or info.get("endTime")
        ),
        metrics=metrics,
        params=params,
        tags=tags,
        raw=run,
    )


class MLflowEvidenceReader:
    """
    Project-specific, read-oriented wrapper around approved MLflow HTTP APIs.

    It translates the DeepFix/Hermes evidence vocabulary (candidate,
    evaluation, trace, manifest) into MLflow experiments, LoggedModels, runs,
    trace metadata, and artifacts.

    MLflow search endpoints legitimately use HTTP POST. Least privilege is
    therefore enforced primarily by the dedicated server-side RBAC identities,
    not by restricting this client to GET requests.

    The class never sets MLFLOW_TRACKING_USERNAME or
    MLFLOW_TRACKING_PASSWORD globally; each instance carries its own Basic Auth
    header so Hermes reader and tracing-service identities can remain separate.
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if query:
            encoded = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None},
                doseq=True,
            )
            url = f"{url}?{encoded}"

        data = None
        headers = dict(self._headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.timeout_seconds,
            ) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MLflowReadError(
                f"MLflow request failed: {method} {path} "
                f"-> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise MLflowReadError(
                f"MLflow request failed: {method} {path}: {exc}"
            ) from exc

        if not payload:
            return {}

        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MLflowReadError(
                f"MLflow returned invalid JSON for {method} {path}"
            ) from exc

        if not isinstance(decoded, dict):
            raise MLflowReadError(
                f"Expected JSON object from {method} {path}; "
                f"got {type(decoded).__name__}"
            )
        return decoded

    # ------------------------------------------------------------------
    # Experiment resolution
    # ------------------------------------------------------------------

    def get_target_experiment(self) -> dict[str, Any]:
        result = self._request(
            "GET",
            "/api/2.0/mlflow/experiments/get-by-name",
            query={"experiment_name": self.config.experiment_name},
        )
        experiment = result.get("experiment")
        if not isinstance(experiment, Mapping):
            raise MLflowReadError(
                "Configured experiment not found or inaccessible: "
                f"{self.config.experiment_name}"
            )
        self._experiment_id = str(experiment["experiment_id"])
        return dict(experiment)

    @property
    def experiment_id(self) -> str:
        if self._experiment_id is None:
            self.get_target_experiment()
        assert self._experiment_id is not None
        return self._experiment_id

    # ------------------------------------------------------------------
    # Low-level run/evaluation reads
    # ------------------------------------------------------------------

    def search_runs(
        self,
        *,
        filter_string: str | None = None,
        max_results: int = 100,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
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
        return self._request(
            "POST",
            "/api/2.0/mlflow/runs/search",
            body=body,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/2.0/mlflow/runs/get",
            query={"run_id": run_id},
        )

    # ------------------------------------------------------------------
    # Low-level trace reads
    # ------------------------------------------------------------------

    def search_traces(
        self,
        *,
        filter_string: str | None = None,
        max_results: int = 100,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "locations": [
                {
                    "type": "MLFLOW_EXPERIMENT",
                    "mlflow_experiment": {
                        "experiment_id": self.experiment_id
                    },
                }
            ],
            "max_results": max_results,
        }
        if filter_string:
            body["filter"] = filter_string
        if order_by:
            body["order_by"] = order_by
        if page_token:
            body["page_token"] = page_token

        return self._request(
            "POST",
            "/api/3.0/mlflow/traces/search",
            body=body,
        )

    def get_trace_info(self, trace_id: str) -> dict[str, Any]:
        safe = urllib.parse.quote(trace_id, safe="")
        return self._request(
            "GET",
            f"/api/3.0/mlflow/traces/{safe}",
        )

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/3.0/mlflow/traces/get",
            query={"trace_id": trace_id},
        )

    # ------------------------------------------------------------------
    # Low-level LoggedModel/artifact reads
    # ------------------------------------------------------------------

    def search_logged_models(
        self,
        *,
        filter_string: str | None = None,
        max_results: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "experiment_ids": [self.experiment_id],
            "max_results": max_results,
        }
        if filter_string:
            body["filter"] = filter_string
        if page_token:
            body["page_token"] = page_token

        return self._request(
            "POST",
            "/api/2.0/mlflow/logged-models/search",
            body=body,
        )

    def get_logged_model(self, model_id: str) -> dict[str, Any]:
        safe = urllib.parse.quote(model_id, safe="")
        return self._request(
            "GET",
            f"/api/2.0/mlflow/logged-models/{safe}",
        )

    def list_logged_model_artifacts(
        self,
        model_id: str,
        *,
        artifact_path: str | None = None,
    ) -> dict[str, Any]:
        safe = urllib.parse.quote(model_id, safe="")
        query = (
            {"artifact_path": artifact_path}
            if artifact_path
            else None
        )
        return self._request(
            "GET",
            (
                f"/api/2.0/mlflow/logged-models/"
                f"{safe}/artifacts/directories"
            ),
            query=query,
        )

    # ------------------------------------------------------------------
    # Candidate-level evidence API
    # ------------------------------------------------------------------

    def find_candidate(self, candidate_id: str) -> dict[str, Any]:
        """
        Backwards-compatible raw LoggedModel lookup.

        Prefer get_candidate() for application/runtime-agent code.
        """
        candidate_id = _validate_candidate_id(candidate_id)
        result = self.search_logged_models(
            filter_string=f"name = '{candidate_id}'"
        )
        models = result.get("models") or []
        if not isinstance(models, list):
            raise MLflowReadError(
                "MLflow LoggedModel search returned invalid 'models'"
            )
        if len(models) != 1:
            raise MLflowReadError(
                f"Expected exactly one LoggedModel named "
                f"{candidate_id!r}; found {len(models)}"
            )
        model = models[0]
        if not isinstance(model, Mapping):
            raise MLflowReadError(
                "MLflow LoggedModel search returned an invalid model"
            )
        return dict(model)

    def get_candidate(self, candidate_id: str) -> Candidate:
        """
        Return one candidate as a stable project-level object.

        Enforces the project invariant candidate_id <-> exactly one LoggedModel.
        """
        candidate_id = _validate_candidate_id(candidate_id)
        candidate = _candidate_from_raw(
            self.find_candidate(candidate_id)
        )
        if candidate.candidate_id != candidate_id:
            raise MLflowReadError(
                "Candidate lookup returned a mismatched LoggedModel: "
                f"requested {candidate_id!r}, got "
                f"{candidate.candidate_id!r}"
            )
        if candidate.experiment_id != self.experiment_id:
            raise MLflowReadError(
                "Candidate belongs to an unexpected experiment: "
                f"{candidate.experiment_id!r} "
                f"(expected {self.experiment_id!r})"
            )
        return candidate

    def get_candidate_manifest(
        self,
        candidate_id: str,
    ) -> dict[str, Any]:
        """
        Download and validate candidate_manifest.json for a candidate.

        This implementation intentionally supports the project's current
        MLflow-served artifact scheme (`mlflow-artifacts:/...`). It does not
        attempt to access S3/GCS/Azure/file URIs directly.

        MLflow's served-artifact endpoint maps:
            mlflow-artifacts:/<path>
        to:
            /api/2.0/mlflow-artifacts/artifacts/<path>
        """
        candidate = self.get_candidate(candidate_id)
        parsed = urllib.parse.urlparse(candidate.artifact_uri)

        if parsed.scheme != "mlflow-artifacts":
            raise MLflowReadError(
                "Candidate artifact URI is not served by MLflow: "
                f"{candidate.artifact_uri!r}. "
                "This reader currently supports only "
                "mlflow-artifacts:/ URIs."
            )
        if parsed.netloc:
            raise MLflowReadError(
                "Unexpected authority component in mlflow-artifacts URI: "
                f"{candidate.artifact_uri!r}"
            )
        if not parsed.path.startswith("/"):
            raise MLflowReadError(
                "Invalid mlflow-artifacts URI path: "
                f"{candidate.artifact_uri!r}"
            )

        artifact_path = (
            "/api/2.0/mlflow-artifacts/artifacts"
            + parsed.path.rstrip("/")
            + "/candidate_manifest.json"
        )
        manifest = self._request("GET", artifact_path)

        missing = sorted(
            _REQUIRED_MANIFEST_KEYS.difference(manifest)
        )
        if missing:
            raise MLflowReadError(
                "candidate_manifest.json is missing required field(s): "
                + ", ".join(missing)
            )

        manifest_candidate_id = manifest.get("candidate_id")
        if manifest_candidate_id != candidate.candidate_id:
            raise MLflowReadError(
                "Candidate manifest identity mismatch: LoggedModel name "
                f"is {candidate.candidate_id!r}, manifest candidate_id "
                f"is {manifest_candidate_id!r}"
            )

        if (
            candidate.config_hash is not None
            and manifest.get("config_hash") != candidate.config_hash
        ):
            raise MLflowReadError(
                "Candidate manifest config_hash does not match "
                "LoggedModel config_hash"
            )

        if (
            candidate.rag_type is not None
            and manifest.get("rag_type") != candidate.rag_type
        ):
            raise MLflowReadError(
                "Candidate manifest rag_type does not match "
                "LoggedModel rag_type"
            )

        configuration = manifest.get("configuration")
        if not isinstance(configuration, Mapping):
            raise MLflowReadError(
                "candidate_manifest.json.configuration must be an object"
            )

        return manifest

    # ------------------------------------------------------------------
    # Evaluation-level evidence API
    # ------------------------------------------------------------------

    def get_evaluation_summary(
        self,
        run_id: str,
    ) -> EvaluationSummary:
        return _evaluation_from_raw(self.get_run(run_id))

    def search_evaluations(
        self,
        *,
        candidate_id: str,
        corpus_id: str | None = None,
        evaluator_version: str | None = None,
        max_results: int = 100,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> EvaluationPage:
        """
        Search evaluation runs without exposing MLflow filter syntax.

        The project contract stores:
            candidate_id
            corpus_id
            evaluator_version
        as run parameters.
        """
        candidate_id = _validate_candidate_id(candidate_id)
        clauses = [
            f"params.candidate_id = '{candidate_id}'"
        ]

        if corpus_id is not None:
            corpus_id = _safe_filter_value(
                corpus_id,
                field_name="corpus_id",
            )
            clauses.append(
                f"params.corpus_id = '{corpus_id}'"
            )

        if evaluator_version is not None:
            evaluator_version = _safe_filter_value(
                evaluator_version,
                field_name="evaluator_version",
            )
            clauses.append(
                f"params.evaluator_version = "
                f"'{evaluator_version}'"
            )

        result = self.search_runs(
            filter_string=" AND ".join(clauses),
            max_results=max_results,
            order_by=order_by,
            page_token=page_token,
        )

        raw_runs = result.get("runs") or []
        if not isinstance(raw_runs, list):
            raise MLflowReadError(
                "MLflow run search returned invalid 'runs'"
            )

        evaluations: list[EvaluationSummary] = []
        for raw_run in raw_runs:
            if not isinstance(raw_run, Mapping):
                raise MLflowReadError(
                    "MLflow run search returned an invalid run object"
                )
            evaluation = _evaluation_from_raw(raw_run)

            # Defensive contract checks: the backend filter should already
            # guarantee these, but fail loudly if stored evidence is malformed.
            if evaluation.candidate_id != candidate_id:
                raise MLflowReadError(
                    "Evaluation search returned a run with mismatched "
                    f"candidate_id: {evaluation.candidate_id!r}"
                )
            if (
                corpus_id is not None
                and evaluation.corpus_id != corpus_id
            ):
                raise MLflowReadError(
                    "Evaluation search returned a run with mismatched "
                    f"corpus_id: {evaluation.corpus_id!r}"
                )
            if (
                evaluator_version is not None
                and evaluation.evaluator_version
                != evaluator_version
            ):
                raise MLflowReadError(
                    "Evaluation search returned a run with mismatched "
                    "evaluator_version: "
                    f"{evaluation.evaluator_version!r}"
                )

            evaluations.append(evaluation)

        token = result.get("next_page_token")
        return EvaluationPage(
            evaluations=tuple(evaluations),
            next_page_token=(
                str(token) if token is not None else None
            ),
        )

    def get_evaluation_runs(
        self,
        candidate_id: str,
    ) -> dict[str, Any]:
        """
        Backwards-compatible raw evaluation-run search.

        Prefer search_evaluations() for new code.
        """
        candidate_id = _validate_candidate_id(candidate_id)
        return self.search_runs(
            filter_string=(
                f"params.candidate_id = '{candidate_id}'"
            )
        )

    # ------------------------------------------------------------------
    # Candidate/question trace evidence API
    # ------------------------------------------------------------------

    def search_candidate_traces(
        self,
        candidate_id: str,
        *,
        corpus_id: str | None = None,
        question_id: str | None = None,
        max_results: int = 100,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Search Target RAG traces by project-level correlation identifiers.
        """
        candidate_id = _validate_candidate_id(candidate_id)
        clauses = [
            (
                "request_metadata.`candidate_id` = "
                f"'{candidate_id}'"
            )
        ]

        if corpus_id is not None:
            corpus_id = _safe_filter_value(
                corpus_id,
                field_name="corpus_id",
            )
            clauses.append(
                "request_metadata.`corpus_id` = "
                f"'{corpus_id}'"
            )

        if question_id is not None:
            question_id = _safe_filter_value(
                question_id,
                field_name="question_id",
            )
            clauses.append(
                "request_metadata.`question_id` = "
                f"'{question_id}'"
            )

        return self.search_traces(
            filter_string=" AND ".join(clauses),
            max_results=max_results,
            order_by=order_by,
            page_token=page_token,
        )

    def get_question_trace(
        self,
        *,
        candidate_id: str,
        question_id: str,
        corpus_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Resolve exactly one evaluation-question trace and return its full data.
        """
        result = self.search_candidate_traces(
            candidate_id,
            corpus_id=corpus_id,
            question_id=question_id,
            max_results=2,
        )
        traces = result.get("traces") or []
        if not isinstance(traces, list):
            raise MLflowReadError(
                "MLflow trace search returned invalid 'traces'"
            )
        if len(traces) != 1:
            raise MLflowReadError(
                "Expected exactly one trace for "
                f"candidate_id={candidate_id!r}, "
                f"question_id={question_id!r}; "
                f"found {len(traces)}"
            )

        trace = traces[0]
        if not isinstance(trace, Mapping):
            raise MLflowReadError(
                "MLflow trace search returned an invalid trace"
            )

        trace_info = trace.get("trace_info")
        if isinstance(trace_info, Mapping):
            trace_id = trace_info.get("trace_id")
        else:
            trace_id = trace.get("trace_id")

        if trace_id is None:
            raise MLflowReadError(
                "Trace search result does not contain trace_id"
            )

        return self.get_trace(str(trace_id))

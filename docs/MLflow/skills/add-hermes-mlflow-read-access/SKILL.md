---
name: add-hermes-mlflow-read-access
description: Wire Hermes to the project's least-privilege MLflow read interface using hermes-reader credentials only. Use when adding or repairing Hermes-side access to Target RAG evidence. Do not configure Hermes tracing, alter MLflow RBAC, or implement RAG optimization logic.
---

# Add Hermes MLflow Read Access

Use the shared `deepfix_mlflow.MLflowEvidenceReader` instead of constructing a generic MLflow client with service-wide credentials.

## Contract

Read completely before editing:

- `.env.example`
- `MLflow logging conventions.txt`
- `deepfix_mlflow/read_client.py`

Hermes read access uses only `MLFLOW_TRACKING_URI`, `MLFLOW_DEV_WORKSPACE`, `MLFLOW_DEV_RAG_EXPERIMENT`, `MLFLOW_HERMES_READER_USERNAME`, and `MLFLOW_HERMES_READER_PASSWORD`.

Never expose `MLFLOW_HERMES_SERVICE_USERNAME` or `MLFLOW_HERMES_SERVICE_PASSWORD` to the read path.

Expected server permission for `hermes-reader`: direct `READ` on `target-rag` in `dev-workspace`, no central write permission, and no `test-workspace` access.

## Workflow

1. Inspect the Hermes repository and identify where external evidence clients belong.
2. Add this package through the repository's normal dependency mechanism, or copy the shared reader module unchanged if necessary.
3. Instantiate `MLflowEvidenceReader.for_hermes_from_env()` only in the evidence-read integration.
4. Keep candidate/evaluation interpretation outside the client. The client retrieves evidence; Hermes decides what to do with it.
5. Do not call mutation endpoints. Search APIs may use HTTP `POST`; read-only means semantic capability, not GET-only networking.
6. Do not fall back to another workspace, experiment, or identity.
7. Do not log credentials or Authorization headers.

## Minimal use

```python
from deepfix_mlflow import MLflowEvidenceReader

reader = MLflowEvidenceReader.for_hermes_from_env()
candidate = reader.find_candidate("candidate_007")
evaluations = reader.get_evaluation_runs("candidate_007")
traces = reader.search_candidate_traces("candidate_007", max_results=20)
```

Retrieve aggregate evidence first, then targeted traces, then full spans only where needed.

## Verification

With `hermes-reader` credentials verify candidate lookup, evaluation-run search, trace search, and selected trace retrieval. Verify mutation attempts fail, `test-workspace` access fails, and service-tracing credentials are absent from the read integration.

Do not weaken server permissions to make a failed read work. Report the specific resource that needs a narrowly scoped read grant.

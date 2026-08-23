---
name: use-mlflow-api
description: Teach an agent how to inspect permitted Target RAG evidence in central MLflow through the project read interface. Use for evidence retrieval and navigation, not for setup, tracing configuration, RAG optimization strategy, or MLflow administration.
---

# Use the MLflow Evidence API

Use `deepfix_mlflow.MLflowEvidenceReader` for permitted central evidence. Do not construct arbitrary mutation requests.

## Evidence structure

```text
dev-workspace
└── target-rag
    ├── candidate LoggedModels
    ├── evaluation runs and aggregate metrics
    └── evaluation/request traces and assessments
```

`test-workspace` is protected and must not be queried or worked around.

Correlation identifiers are `candidate_id`, `corpus_id`, and `question_id` when applicable. Native MLflow IDs (`model_id`, `run_id`, `trace_id`) remain native IDs.

## Context-efficient retrieval order

1. Resolve the candidate LoggedModel by `candidate_id`.
2. Read compact candidate parameters such as `rag_type` and `config_hash`.
3. Read aggregate evaluation runs and metrics.
4. Search trace metadata using a narrow filter.
5. Fetch full spans only for traces needed to explain an aggregate result or failure.
6. Read large artifacts only when narrower evidence is insufficient.

Do not dump all traces or spans into agent context by default.

## Python interface

```python
from deepfix_mlflow import MLflowEvidenceReader

reader = MLflowEvidenceReader.for_hermes_from_env()
model = reader.find_candidate("candidate_007")
runs = reader.get_evaluation_runs("candidate_007")
trace_infos = reader.search_candidate_traces("candidate_007", max_results=20)
```

Use `reader.get_trace(trace_id)` only after selecting a relevant trace.

## Boundaries

- Do not mutate MLflow resources.
- Do not access `test-workspace`.
- Do not infer or request hidden evaluation evidence.
- Do not decide how to optimize the RAG; this skill retrieves and explains evidence structure only.
- Do not request broad workspace access when a resource-specific read grant is missing.

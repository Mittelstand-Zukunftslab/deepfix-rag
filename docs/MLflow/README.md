# mlflow-deepfix

Reusable integration material for the project's central MLflow workstream.

## Repository structure

```text
.
├── .env.example
├── docker-compose.yml
├── MLflow logging conventions.txt
├── mlflow_helpers.py
├── pyproject.toml
├── deepfix_mlflow/
│   ├── __init__.py
│   ├── read_client.py
│   └── hermes_tracing.py
└── skills/
    ├── add-rag-mlflow-tracing/
    │   └── SKILL.md
    ├── add-hermes-mlflow-read-access/
    │   └── SKILL.md
    ├── add-hermes-mlflow-tracing/
    │   └── SKILL.md
    └── use-mlflow-api/
        └── SKILL.md
```

## Security model

The central identities are intentionally separated:

- `rag-service`: writes Target RAG and approved evaluation evidence;
- `deepfix-reader`: reads visible Target RAG evidence;
- `hermes-reader`: reads visible Target RAG evidence only;
- `hermes-service`: writes Hermes traces only.

The Python read client uses per-instance Basic Auth headers and an explicit `X-MLFLOW-WORKSPACE` header. It does not mutate process-global MLflow SDK credentials, so Hermes read credentials and tracing credentials remain separate.

The Hermes tracing helper generates a deployment-time Basic Auth header for `hermes-service` and a `hermes-otel` OTLP backend fragment. Real credentials and the derived Authorization value must remain outside tracked files.

## Contract files

- `MLflow logging conventions.txt` defines the canonical evidence layout, including the mandatory `rag_request` semantic root span.
- `mlflow_helpers.py` defines the canonical RAG configuration validation, normalization, and hash calculation.
- `.env.example` defines the canonical central resource and credential variable names.

## Ownership boundary

This repository supplies the MLflow contracts, reusable access/tracing integration helpers, setup skills, and validation expectations. Target RAG, DeepFix, and Hermes owners remain responsible for application logic and for deciding how retrieved evidence is used.

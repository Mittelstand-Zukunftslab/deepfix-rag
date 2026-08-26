"""Build the candidate manifest and create/reuse the candidate LoggedModel."""

from __future__ import annotations

import json
import os

import mlflow
from dotenv import load_dotenv

from deepfix_rag.evaluation.mlflow_helpers import compute_config_hash

load_dotenv()

os.environ["MLFLOW_TRACKING_USERNAME"] = os.environ["MLFLOW_RAG_USERNAME"]
os.environ["MLFLOW_TRACKING_PASSWORD"] = os.environ["MLFLOW_RAG_PASSWORD"]

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_workspace(os.environ["MLFLOW_DEV_WORKSPACE"])


def build_manifest() -> dict:
    manifest = {
        "schema_version": 1,
        "candidate_id": os.environ["MLFLOW_CANDIDATE_ID"],
        "rag_type": "vector_rag",
        "configuration": {
            "chunking": {
                "strategy": "none",
                "chunk_size": 0,
                "chunk_overlap": 0,
                "parameters": {},
            },
            "embedding": {
                "provider": "hpi",
                "model": "qwen/qwen3-embedding-8b",
                "parameters": {"batch_size": 32},
            },
            "vector_store": {
                "type": "lancedb",
                "parameters": {"table": "documents"},
            },
            "query_rewriting": {
                "enabled": False,
                "strategy": None,
                "provider": None,
                "model": None,
                "parameters": {},
            },
            "retrieval": {
                "strategy": "hybrid",
                "top_k": 10,
                "parameters": {"hybrid_alpha": 0.5},
            },
            "reranking": {
                "enabled": False,
                "type": None,
                "model": None,
                "top_n": None,
                "parameters": {},
            },
            "context_assembly": {
                "strategy": "concatenation",
                "max_chunks": 10,
                "parameters": {"separator": "\n\n"},
            },
            "generation": {
                "provider": "hpi",
                "model": "openai/openrouter/qwen/qwen3.8-27b",
                "parameters": {"temperature": 0.0, "max_tokens": 1024},
            },
            "prompts": {
                "system": "prompts:/target-rag-system-main/1",
                "query_rewrite": None,
            },
        },
    }
    return manifest

def _get_param(model, key: str) -> str | None:
    params = model.params
    if isinstance(params, dict):
        return params.get(key)
    for p in params:
        if getattr(p, "key", None) == key:
            return getattr(p, "value", None)
    return None

def ensure_candidate_logged_model() -> str:
    candidate_id = os.environ["MLFLOW_CANDIDATE_ID"]
    manifest = build_manifest()
    config_hash = compute_config_hash(manifest)
    manifest["config_hash"] = config_hash

    experiment_name = os.environ["MLFLOW_DEV_RAG_EXPERIMENT"]
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise RuntimeError(f"Experiment '{experiment_name}' does not exist.")

    existing = mlflow.search_logged_models(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"name = '{candidate_id}'",
        output_format="list",
    )

    if len(existing) == 0:
        model = mlflow.initialize_logged_model(
            name=candidate_id,
            experiment_id=experiment.experiment_id,
            params={"rag_type": manifest["rag_type"], "config_hash": config_hash},
        )
        with open("candidate_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        client = mlflow.MlflowClient()
        client.log_model_artifact(model.model_id, "candidate_manifest.json")
        mlflow.finalize_logged_model(model.model_id, status="READY")

        print(f"CREATED new candidate '{candidate_id}' with model_id={model.model_id}")
        print(f"config_hash={config_hash}")
        return model.model_id

    if len(existing) > 1:
        raise RuntimeError(f"Multiple LoggedModels named '{candidate_id}' found.")

    existing_model = existing[0]
    existing_hash = _get_param(existing_model, "config_hash")
    if existing_hash != config_hash:
        raise RuntimeError(
            f"Candidate '{candidate_id}' already exists with config_hash "
            f"'{existing_hash}', but current config hashes to '{config_hash}'."
        )
    print(f"REUSED existing candidate '{candidate_id}' with model_id={existing_model.model_id}")
    print(f"config_hash={config_hash}")
    return existing_model.model_id


if __name__ == "__main__":
    ensure_candidate_logged_model()
"""Pydantic schemas and Optuna suggestion strategies for RAG Hyperparameter Optimization (HPO)."""

from pathlib import Path
from typing import Any

import optuna
from pydantic import BaseModel, Field, model_validator
import yaml

from deepfix_rag.config.schema import (
    FusionMode,
    RAGConfig,
    RetrievalBackend,
)


class DenseSearchSpace(BaseModel):
    """Parameter bounds for Dense vector retrieval paradigm."""

    chunk_size_min: int = Field(default=128, ge=64, le=4096)
    chunk_size_max: int = Field(default=2048, ge=64, le=4096)
    chunk_size_step: int = Field(default=64, ge=1)

    chunk_overlap_min: int = Field(default=0, ge=0)
    chunk_overlap_max: int = Field(default=256, ge=0)
    chunk_overlap_step: int = Field(default=16, ge=1)

    top_k_min: int = Field(default=1, ge=1)
    top_k_max: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "DenseSearchSpace":
        if self.chunk_size_min >= self.chunk_size_max:
            raise ValueError("chunk_size_min must be strictly less than chunk_size_max")
        if self.chunk_overlap_min > self.chunk_overlap_max:
            raise ValueError("chunk_overlap_min must be <= chunk_overlap_max")
        if self.top_k_min > self.top_k_max:
            raise ValueError("top_k_min must be <= top_k_max")
        return self

    def suggest_dense(self, trial: optuna.Trial, tune_chunking: bool = False) -> dict[str, Any]:
        """Sample trial parameters for dense retrieval."""
        params: dict[str, Any] = {}

        if tune_chunking:
            chunk_size = trial.suggest_int(
                "chunking.size",
                self.chunk_size_min,
                self.chunk_size_max,
                step=self.chunk_size_step,
            )
            raw_max = min(self.chunk_overlap_max, chunk_size - 1)
            step = self.chunk_overlap_step
            max_overlap = max(self.chunk_overlap_min, (raw_max // step) * step)
            trial.suggest_int(
                "chunking.overlap",
                self.chunk_overlap_min,
                max_overlap,
                step=step,
            )
            params["chunking.size"] = chunk_size
            params["chunking.overlap"] = trial.params["chunking.overlap"]

        params["retrieval.top_k"] = trial.suggest_int(
            "retrieval.top_k", self.top_k_min, self.top_k_max
        )
        return params


class HybridSearchSpace(DenseSearchSpace):
    """Parameter bounds for Hybrid (Dense + BM25) retrieval paradigm."""

    bm25_k1_min: float = Field(default=0.5, ge=0.0)
    bm25_k1_max: float = Field(default=3.0, ge=0.0)
    bm25_b_min: float = Field(default=0.1, ge=0.0, le=1.0)
    bm25_b_max: float = Field(default=1.0, ge=0.0, le=1.0)

    dense_weight_min: float = Field(default=0.0, ge=0.0, le=1.0)
    dense_weight_max: float = Field(default=1.0, ge=0.0, le=1.0)

    fusion_modes: list[FusionMode] = Field(
        default_factory=lambda: [
            FusionMode.RECIPROCAL_RANK,
            FusionMode.RELATIVE_SCORE,
            FusionMode.DIST_BASED_SCORE,
            FusionMode.SIMPLE,
        ]
    )

    @model_validator(mode="after")
    def validate_hybrid_bounds(self) -> "HybridSearchSpace":
        if self.bm25_k1_min > self.bm25_k1_max:
            raise ValueError("bm25_k1_min must be <= bm25_k1_max")
        if self.bm25_b_min > self.bm25_b_max:
            raise ValueError("bm25_b_min must be <= bm25_b_max")
        if self.dense_weight_min > self.dense_weight_max:
            raise ValueError("dense_weight_min must be <= dense_weight_max")
        return self

    def suggest_hybrid(self, trial: optuna.Trial, tune_chunking: bool = False) -> dict[str, Any]:
        """Sample trial parameters for hybrid retrieval."""
        params = self.suggest_dense(trial, tune_chunking=tune_chunking)
        params["retrieval.bm25_k1"] = trial.suggest_float(
            "retrieval.bm25_k1", self.bm25_k1_min, self.bm25_k1_max
        )
        params["retrieval.bm25_b"] = trial.suggest_float(
            "retrieval.bm25_b", self.bm25_b_min, self.bm25_b_max
        )
        dense_w = trial.suggest_float(
            "retrieval.dense_weight", self.dense_weight_min, self.dense_weight_max
        )
        params["retrieval.dense_weight"] = round(dense_w, 4)
        params["retrieval.bm25_weight"] = round(1.0 - dense_w, 4)
        params["retrieval.fusion_mode"] = trial.suggest_categorical(
            "retrieval.fusion_mode", [m.value for m in self.fusion_modes]
        )
        return params


class GraphSearchSpace(BaseModel):
    """Parameter bounds for Property Graph RAG retrieval paradigm."""

    extraction_schemas: list[str] = Field(
        default_factory=lambda: ["default", "entity_relationship", "domain_legal"]
    )
    max_paths_per_chunk_min: int = Field(default=1, ge=1, le=10)
    max_paths_per_chunk_max: int = Field(default=10, ge=1, le=10)

    traversal_hops_min: int = Field(default=1, ge=1, le=3)
    traversal_hops_max: int = Field(default=3, ge=1, le=3)

    dense_weight_min: float = Field(default=0.0, ge=0.0, le=1.0)
    dense_weight_max: float = Field(default=1.0, ge=0.0, le=1.0)

    fusion_modes: list[FusionMode] = Field(
        default_factory=lambda: [
            FusionMode.RECIPROCAL_RANK,
            FusionMode.RELATIVE_SCORE,
            FusionMode.DIST_BASED_SCORE,
            FusionMode.SIMPLE,
        ]
    )

    top_k_min: int = Field(default=1, ge=1)
    top_k_max: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def validate_graph_bounds(self) -> "GraphSearchSpace":
        if self.max_paths_per_chunk_min > self.max_paths_per_chunk_max:
            raise ValueError("max_paths_per_chunk_min must be <= max_paths_per_chunk_max")
        if self.traversal_hops_min > self.traversal_hops_max:
            raise ValueError("traversal_hops_min must be <= traversal_hops_max")
        if self.dense_weight_min > self.dense_weight_max:
            raise ValueError("dense_weight_min must be <= dense_weight_max")
        if self.top_k_min > self.top_k_max:
            raise ValueError("top_k_min must be <= top_k_max")
        return self

    def suggest_graph(self, trial: optuna.Trial, tune_chunking: bool = False) -> dict[str, Any]:
        """Sample trial parameters for graph retrieval."""
        params: dict[str, Any] = {
            "retrieval.graph_extraction_schema": trial.suggest_categorical(
                "retrieval.graph_extraction_schema", self.extraction_schemas
            ),
            "retrieval.graph_max_paths_per_chunk": trial.suggest_int(
                "retrieval.graph_max_paths_per_chunk",
                self.max_paths_per_chunk_min,
                self.max_paths_per_chunk_max,
            ),
            "retrieval.graph_traversal_hops": trial.suggest_int(
                "retrieval.graph_traversal_hops",
                self.traversal_hops_min,
                self.traversal_hops_max,
            ),
            "retrieval.top_k": trial.suggest_int(
                "retrieval.top_k", self.top_k_min, self.top_k_max
            ),
            "retrieval.fusion_mode": trial.suggest_categorical(
                "retrieval.fusion_mode", [m.value for m in self.fusion_modes]
            ),
        }
        dense_w = trial.suggest_float(
            "retrieval.dense_weight",
            self.dense_weight_min,
            self.dense_weight_max,
        )
        graph_w = 1.0 - dense_w
        params["retrieval.dense_weight"] = round(dense_w, 4)
        params["retrieval.graph_weight"] = round(graph_w, 4)
        params["retrieval.graph_fusion_weights"] = [round(dense_w, 4), round(graph_w, 4)]
        return params


def resolve_metric_candidates(metric_name: str, cutoff_k: int | None = None) -> list[str]:
    """Generate canonical and cutoff-parameterized lookup keys for a metric."""
    name = str(metric_name).lower().strip()
    candidates: list[str] = [name]

    if "recall" in name:
        candidates.append("recall")
        if cutoff_k is not None:
            candidates.append(f"R@{cutoff_k}")
    elif "ndcg" in name:
        candidates.append("ndcg")
        if cutoff_k is not None:
            candidates.append(f"nDCG@{cutoff_k}")
    elif "precision" in name:
        candidates.append("precision")
        if cutoff_k is not None:
            candidates.append(f"P@{cutoff_k}")
    elif "faithfulness" in name:
        candidates.append("faithfulness")
    elif "relevan" in name:
        candidates.extend(["answer_relevancy", "contextual_relevancy"])

    return list(dict.fromkeys(candidates))


class HPOMetricConfig(BaseModel):
    """Configuration for target optimization metrics and evaluation alias resolution."""

    targets: list[str] = Field(
        default_factory=lambda: ["recall", "faithfulness"],
        description="Target metric names to optimize during generation + retrieval HPO.",
    )
    retrieval_only_targets: list[str] = Field(
        default_factory=lambda: ["recall", "ndcg"],
        description="Target metric names to optimize in retrieval-only mode.",
    )
    aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Optional custom alias mappings overriding standard resolution candidates.",
    )


class HPOSearchSpace(BaseModel):
    """Composite hyperparameter search space across retrieval paradigms."""

    dense: DenseSearchSpace = Field(default_factory=DenseSearchSpace)
    hybrid: HybridSearchSpace = Field(default_factory=HybridSearchSpace)
    graph: GraphSearchSpace = Field(default_factory=GraphSearchSpace)
    metrics: HPOMetricConfig = Field(default_factory=HPOMetricConfig)

    def suggest_parameters(
        self,
        trial: optuna.Trial,
        paradigm: RetrievalBackend | str,
        tune_chunking: bool = False,
    ) -> dict[str, Any]:
        """Suggest dot-notation configuration overrides for the given retrieval paradigm."""
        paradigm_str = (
            paradigm.value
            if isinstance(paradigm, RetrievalBackend)
            else str(paradigm).lower().strip()
        )

        if paradigm_str == RetrievalBackend.DENSE.value:
            return self.dense.suggest_dense(trial, tune_chunking=tune_chunking)
        elif paradigm_str in (RetrievalBackend.HYBRID.value, RetrievalBackend.BM25.value):
            return self.hybrid.suggest_hybrid(trial, tune_chunking=tune_chunking)
        elif paradigm_str == RetrievalBackend.GRAPH.value:
            return self.graph.suggest_graph(trial, tune_chunking=tune_chunking)
        elif paradigm_str == RetrievalBackend.MIXED.value:
            dense_params = self.dense.suggest_dense(trial, tune_chunking=tune_chunking)
            params = {**dense_params}
            params["retrieval.bm25_k1"] = trial.suggest_float(
                "retrieval.bm25_k1", self.hybrid.bm25_k1_min, self.hybrid.bm25_k1_max
            )
            params["retrieval.bm25_b"] = trial.suggest_float(
                "retrieval.bm25_b", self.hybrid.bm25_b_min, self.hybrid.bm25_b_max
            )
            params["retrieval.graph_extraction_schema"] = trial.suggest_categorical(
                "retrieval.graph_extraction_schema", self.graph.extraction_schemas
            )
            params["retrieval.graph_max_paths_per_chunk"] = trial.suggest_int(
                "retrieval.graph_max_paths_per_chunk",
                self.graph.max_paths_per_chunk_min,
                self.graph.max_paths_per_chunk_max,
            )
            params["retrieval.graph_traversal_hops"] = trial.suggest_int(
                "retrieval.graph_traversal_hops",
                self.graph.traversal_hops_min,
                self.graph.traversal_hops_max,
            )
            dense_w = trial.suggest_float("retrieval.dense_weight", 0.0, 1.0)
            bm25_w = trial.suggest_float("retrieval.bm25_weight", 0.0, 1.0)
            graph_w = trial.suggest_float("retrieval.graph_weight", 0.0, 1.0)
            params["retrieval.dense_weight"] = round(dense_w, 4)
            params["retrieval.bm25_weight"] = round(bm25_w, 4)
            params["retrieval.graph_weight"] = round(graph_w, 4)
            params["retrieval.fusion_mode"] = trial.suggest_categorical(
                "retrieval.fusion_mode", [m.value for m in self.hybrid.fusion_modes]
            )
            return params
        else:
            raise ValueError(f"Unsupported retrieval paradigm for HPO: {paradigm}")


class HPOConfig(BaseModel):
    """Top-level HPO configuration schema containing study parameters, search spaces, and metric definitions."""

    version: str = "1.0"
    base_config: str | Path | RAGConfig = Field(
        default="configs/default.yaml",
        description="Path to base RAG config file or inline RAGConfig.",
    )
    study_name: str | None = Field(default=None, description="Optional study name for Optuna.")
    storage_uri: str = Field(
        default="sqlite:///:memory:", description="Storage URI for Optuna study."
    )
    n_trials: int = Field(
        default=10, ge=1, description="Number of Optuna optimization trials to execute."
    )
    tune_chunking: bool = Field(
        default=False, description="Whether to optimize chunk size & overlap across trials."
    )
    retrieval_only: bool = Field(
        default=False,
        description="Optimize retrieval-only metrics (NDCG and Recall) skipping LLM generation evaluation.",
    )
    dataset: str | None = Field(
        default=None, description="Optional dataset path or HF repo ID override."
    )
    split: str | None = Field(default=None, description="Optional dataset split override.")
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Optional maximum number of QA records per evaluation trial.",
    )
    output: Path | str | None = Field(
        default=None,
        description="Optional destination path to export the best configuration YAML.",
    )
    metrics: HPOMetricConfig = Field(
        default_factory=HPOMetricConfig, description="Target metrics configuration."
    )
    search_space: HPOSearchSpace = Field(
        default_factory=HPOSearchSpace,
        description="Parameter bounds across dense, hybrid, and graph retrieval paradigms.",
    )

    def resolve_base_config(self, base_dir: Path | str | None = None) -> RAGConfig:
        """Resolve and return a validated RAGConfig instance."""
        if isinstance(self.base_config, RAGConfig):
            return self.base_config

        if isinstance(self.base_config, dict):
            return RAGConfig.model_validate(self.base_config)

        if isinstance(self.base_config, (str, Path)):
            path = Path(self.base_config)
            if not path.is_absolute():
                if path.exists():
                    resolved_path = path
                elif base_dir is not None and (Path(base_dir) / path).exists():
                    resolved_path = Path(base_dir) / path
                else:
                    resolved_path = path
            else:
                resolved_path = path

            if not resolved_path.exists():
                raise FileNotFoundError(f"Base RAG configuration file not found: {resolved_path}")

            with open(resolved_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            return RAGConfig.model_validate(data)

        raise ValueError(f"Unsupported base_config type: {type(self.base_config)}")


def load_hpo_config(config_path: Path | str) -> HPOConfig:
    """Load and validate an HPOConfig from a YAML file, with fallback for pure RAGConfig files."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"HPO configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if isinstance(data, dict):
        is_pure_rag = (
            ("retrieval" in data or "chunking" in data or "generation" in data)
            and "search_space" not in data
            and "base_config" not in data
            and "metrics" not in data
        )
        if is_pure_rag:
            rag_cfg = RAGConfig.model_validate(data)
            return HPOConfig(base_config=rag_cfg)

    return HPOConfig.model_validate(data)

"""JSON structured diagnostic reports."""

from pydantic import BaseModel, Field


class FailureInstance(BaseModel):
    """Details for a single failing query instance."""

    query_id: str
    query_text: str
    failure_category: str  # recall, precision, oov, hallucination
    score: float
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    ground_truth_doc_ids: list[str] = Field(default_factory=list)
    remediation_hint: str | None = None


class DiagnosticReport(BaseModel):
    """Aggregated diagnostic run report."""

    dataset_name: str
    total_queries: int = 0
    failed_queries: int = 0
    failure_distribution: dict[str, int] = Field(default_factory=dict)
    instances: list[FailureInstance] = Field(default_factory=list)

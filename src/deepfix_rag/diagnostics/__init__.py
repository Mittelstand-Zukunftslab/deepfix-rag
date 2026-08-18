"""Deterministic diagnostic engine and reporting."""

from deepfix_rag.diagnostics.engine import diagnose_failures
from deepfix_rag.diagnostics.reporter import DiagnosticReport

__all__ = ["DiagnosticReport", "diagnose_failures"]

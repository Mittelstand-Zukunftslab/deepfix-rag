"""DeepFix CLI entrypoint."""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import typer

from deepfix_rag.cli import config, diagnostic, eval, export, index

app = typer.Typer(
    name="deepfix",
    help="Deterministic CLI for RAG optimization, indexing, diagnostics, and evaluation.",
    no_args_is_help=True,
)

# Register subcommands
app.command(
    name="eval", help="Run DeepEval evaluation on a dataset using a given RAG configuration."
)(eval.eval_cmd)
app.command(
    name="index", help="Build vector/lexical index for a dataset using a given configuration."
)(index.index_cmd)
app.command(
    name="run-diagnostic",
    help="Run deterministic failure mode diagnostics on retrieval/grounding.",
)(diagnostic.diagnostic_cmd)
app.command(
    name="mutate-config", help="Validate and apply parameter overrides to a RAG config YAML."
)(config.mutate_config_cmd)
app.command(
    name="diff-config", help="Compare two RAG configuration YAMLs and output structural diff."
)(config.diff_config_cmd)
app.command(name="export", help="Export optimized RAG configuration bundle.")(export.export_cmd)


if __name__ == "__main__":
    app()

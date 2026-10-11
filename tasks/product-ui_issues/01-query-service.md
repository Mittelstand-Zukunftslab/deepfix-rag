# 01 — Query service in the domain layer (US-001)

**What to build:** A long-lived domain service that loads a built index once and answers both retrieval and RAG requests. Retrieval returns ranked passages without calling the generation model. RAG returns those passages plus the generated answer. Existing CLI commands for indexing and evaluation keep working unchanged.

**Reference:** tasks/prd-product-ui.md, `### US-001: Workspace query service in the domain layer`

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] A domain service is constructed with a config and an index directory, loads the existing index once, and keeps it in memory.
- [ ] Retrieval runs retrieval and reranking only, regardless of the config's retrieval-only flag, and never calls the generation model.
- [ ] Answering runs retrieval, reranking, and synthesis and returns both the passages and the generated answer. The generated answer is typed as `str | None`.
- [ ] Every passage keeps its parent document id, score, and chunk text, in rank order.
- [ ] Calling retrieval twice on the same service does not rebuild or reload the index. A test asserts this without mocks, by timing or by counting index loads through a real counter on the service.
- [ ] Existing CLI commands for indexing and evaluation keep working unchanged.
- [ ] Tests use the populated sample index and sample config fixtures. No mocks of the pipeline.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

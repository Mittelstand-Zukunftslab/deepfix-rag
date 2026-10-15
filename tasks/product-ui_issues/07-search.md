# 07 — Search the shared corpus

**What to build:** A signed-in member searches the shared index and sees ranked passages, so they can judge retrieval without waiting for an answer. Each search is stored as an interaction with a snapshot of every hit, and traced in MLflow, so a later vote still refers to the exact passages that were shown. This replaces the mock Search screen.

**Reference:** tasks/prd-product-ui.md, `### US-009: Search the shared corpus`

**Blocked by:**

- 01 — Query service in the domain layer
- 06 — Full blue-green index build and index status

**Status:** ready-for-agent

- [ ] Search accepts a query and calls the query service's retrieval method. It never calls the generation model.
- [ ] The API records an interaction (id, kind retrieval, user, query, build id, champion config path, created time) and a snapshot of every returned hit: rank from 1, document id, text, passage key (SHA-256 of the chunk text), and score.
- [ ] The request is traced with MLflow tracing in experiment `deepfix_product`. The trace id is stored on the interaction and the interaction id is set as a trace tag. A tracing failure is logged and does not fail the request; the interaction is stored without a trace id.
- [ ] `pyproject.toml` requires `mlflow>=3`.
- [ ] The response returns the interaction id and the hits, each with rank, document id, document filename, text, score, and the current user's vote (up, down, or null).
- [ ] The number of hits follows the champion config's retrieval depth, or the rerank depth when reranking is enabled, capped at a display limit setting (default 10).
- [ ] An empty query returns 400. No ready index returns 503.
- [ ] The Search screen has one query field, a Search button, and a result list showing rank, filename, score, and text. The mock search service and invoice data are removed.
- [ ] No hits: "No passages matched "<query>"."
- [ ] Typecheck passes.
- [ ] Verify in the browser.

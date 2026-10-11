# 15 — Promotion gate and holdout check

**What to build:** After an optimization run returns a candidate, API code decides whether it becomes the champion. The candidate must validate and use an allowed retrieval backend. The API then scores candidate and champion itself: on dev, the candidate must beat the champion by the quality gate; on holdout, which exists only in memory inside the API, it must not regress. Numbers the agent reports are stored for reference and never trusted. Query-time changes reload the query service against the current index. Index-time changes rebuild the index beside the serving index and switch the config and the index together only after that build succeeds. The agent never promotes itself.

**Reference:** tasks/prd-product-ui.md, `### US-014: Optimization run lifecycle owned by the API`

**Blocked by:**

- 02 — Multi-label, reference-optional eval records
- 06 — Full blue-green index build and index status
- 14 — Optimization run lifecycle

**Status:** ready-for-agent

- [ ] API code reads the candidate named in the run's `result.json`. It does not ask the agent to promote itself.
- [ ] The API rejects the candidate, with status rejected and a recorded reason, unless the candidate validates as a config and its retrieval backend is dense, hybrid, or bm25. Location fields in the candidate are replaced by the API's own.
- [ ] The API scores candidate and champion on the snapshot's dev set itself. It rejects the candidate unless the primary metric delta is at least the configured minimum and no other metric in the delta is negative.
- [ ] The API recomputes holdout in memory from the database and scores candidate and champion on it with retrieval-only eval, building a candidate index first when index-time keys changed. It rejects the candidate unless the primary metric delta is at least 0 and the candidate wins at least as many holdout questions as it loses on that metric. Holdout is never written to disk or MLflow.
- [ ] The run record stores dev and holdout deltas and holdout wins and losses.
- [ ] If all checks pass and only query-time keys changed, the API archives the previous champion under the configs history directory, atomically replaces the champion config, reloads the query service against the current index, and marks the run promoted.
- [ ] If an index-time key changed, the API first performs a full blue-green rebuild with the candidate. Only after that succeeds does it atomically switch both the serving-index pointer and the champion config. If it fails, the old index and champion continue serving and the run is marked failed.
- [ ] Integration tests exercise rejection and promotion by calling the lifecycle functions with temporary workspaces, the sample corpus, and a hand-written result file, in retrieval-only mode. Those tests do not start the agent. No mocks of the pipeline or eval runner.

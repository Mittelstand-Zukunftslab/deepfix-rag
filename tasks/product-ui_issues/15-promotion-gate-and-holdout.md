# 15 — Promotion gate and holdout check

**What to build:** After an optimization run returns a candidate, API code decides whether it becomes the champion. The candidate must validate, use an allowed retrieval backend, beat the champion on dev by the existing quality gate, and not regress on holdout, which only the API can read. Query-time changes reload the query service against the current index. Index-time changes rebuild the index beside the serving index and switch the config and the index together only after that build succeeds. The agent never promotes itself.

**Blocked by:**

- 02 — Multi-label, reference-optional eval records
- 06 — Full blue-green index build and index status
- 14 — Optimization run lifecycle

**Status:** ready-for-agent

- [ ] When the conversation returns, API code reads the candidate written under that run directory. It does not ask the agent to promote itself.
- [ ] The API rejects the candidate, with status rejected and a recorded reason, unless the candidate validates as a config and its retrieval backend is dense, hybrid, or bm25.
- [ ] The API rejects the candidate unless, on the submitted dev comparison, the primary metric delta is at least 0.025 and no other metric in the delta is negative.
- [ ] The API rejects the candidate unless holdout has at least 5 questions. The API itself builds a candidate index when index-time keys changed, runs retrieval-only eval for the candidate and the champion on holdout, and requires the primary retrieval metric delta to be at least 0.
- [ ] If all checks pass and only query-time keys changed, the API archives the previous champion, atomically replaces the champion config, reloads the query service against the current index, and marks the run promoted.
- [ ] If an index-time key changed, the API first performs a full blue-green rebuild with the candidate. Only after that succeeds does it atomically switch both the serving-index pointer and the champion config. If it fails, the old index and champion continue serving and the run is marked failed.
- [ ] Integration tests exercise rejection and promotion by calling the lifecycle functions with temporary workspaces and the sample corpus in retrieval-only mode. Those tests do not construct an agent. No mocks of the pipeline or eval runner.

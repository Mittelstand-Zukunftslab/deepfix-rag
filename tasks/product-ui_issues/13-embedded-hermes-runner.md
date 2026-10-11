# 13 — Hermes agent runner in a child process

**What to build:** The API can run Hermes through its Python library, one new agent per optimization run, in a child process it starts, watches, and can kill. The child uses a dedicated Hermes virtual environment, so Hermes dependencies stay out of the `deepfix_rag` environment and a crash cannot take down the API. The agent works inside the workspace, shells out to the API's own `deepfix` binary, and writes candidate configs and indexes only under that run's directory. It never sees the product database or session secrets. It does not use the Hermes gateway.

**Reference:** tasks/prd-product-ui.md, `### US-003: Hermes agent runner in a child process` and `### US-013: Workspace layout for the agent`

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] A pinned Hermes commit, the checkout path, and the interpreter of a dedicated Hermes virtual environment are recorded in `tasks/notes-hermes-integration.md`. That environment can import `AIAgent` from `run_agent` and does not have `deepfix_rag` installed.
- [ ] A standalone runner script with no `deepfix_rag` imports runs under the Hermes interpreter. It reads `runs/<run_id>/job.json` (prompt, model settings, iteration limit), builds one agent with quiet mode, memory skipped, and the job's iteration limit (default 15), runs one conversation for that run id, and writes `runs/<run_id>/agent_output.json` with the final response and any error.
- [ ] A domain helper writes the job file, starts the runner as a child process with the workspace as its working directory, and waits for it. When the run exceeds the configured timeout (default 3 hours), the helper kills the whole process group and reports a timeout.
- [ ] The child's environment is built from an explicit allowlist: LLM base URL, key, and model; the MLflow tracking URI; the diagnostic server URL; and a `PATH` whose first entry is the API virtual environment's `bin/`, so `deepfix` is the same code revision as the API. The product database path, the API-private directory, and session secrets are not in the environment or the prompt. No `uv tool install` is needed.
- [ ] The agent's terminal tool is enabled. Indexing, eval, and config mutation happen only when the agent shells out to `deepfix`. The runner does not call the Hermes gateway on ports 8642 or 9119. There is no second Hermes container and no internal token.
- [ ] The agent may write candidate configs and candidate indexes only under the workspace runs directory for that run id.
- [ ] A test, skipped only when the Hermes interpreter is not configured or cannot import the agent library, runs one agent turn whose prompt is to execute `deepfix --help` and asserts the command's usage text appears in the final response.
- [ ] A test starts a runner that sleeps past a short timeout and asserts the child process is gone afterwards.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

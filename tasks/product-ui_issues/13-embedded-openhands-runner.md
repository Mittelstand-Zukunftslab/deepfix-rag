# 13 — OpenHands agent runner in a child process

**What to build:** The API can run an OpenHands agent through the Software Agent SDK, one new conversation per optimization run, in a child process it starts, watches, and can kill. The child uses a dedicated OpenHands virtual environment, so SDK dependencies stay out of the `deepfix_rag` environment and a crash cannot take down the API. The agent works inside the workspace, shells out to the API's own `deepfix` binary, and writes candidate configs and indexes only under that run's directory. It never sees the product database or session secrets. Its traces go to the MLflow tracking server.

**Reference:** tasks/prd-product-ui.md, `### US-003: OpenHands agent runner in a child process` and `### US-013: Workspace layout for the agent`

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] The pinned `openhands-sdk` and `openhands-tools` version, the virtual environment path, and its interpreter (`OPENHANDS_PYTHON`) are recorded in `tasks/notes-openhands-integration.md`. That environment can import `openhands.sdk` and the terminal and file editor tools, and does not have `deepfix_rag` installed.
- [ ] A standalone runner script with no `deepfix_rag` imports runs under the OpenHands interpreter. It reads `runs/<run_id>/job.json` (prompt, model settings, iteration limit), builds one agent with the terminal and file editor tools, runs one conversation for that run id with the job's iteration limit (default 15), and writes `runs/<run_id>/agent_output.json` with the final response and any error. A conversation that does not end in `finished` is reported as an error.
- [ ] OpenRouter model ids are routed through LiteLLM with the `openrouter/` prefix.
- [ ] The prompt is pursued with the SDK's goal completion loop. After every finished agent run, a separate judge LLM audits the transcript, including the agent's commands and its finish message. Unmet requirements are sent back to the agent for at most `OPTIMIZATION_GOAL_ROUNDS` rounds (default 3). A goal still unmet after the last round is reported as an error with the judge's explanation, and the verdict is recorded in `agent_output.json`.
- [ ] A domain helper writes the job file, starts the runner as a child process with the workspace as its working directory, and waits for it. When the run exceeds the configured timeout (default 3 hours), the helper kills the whole process group and reports a timeout.
- [ ] The child's environment is built from an explicit allowlist: LLM base URL, key, and model; the MLflow tracking URI; the diagnostic server URL; the agent's OTLP settings; and a `PATH` whose first entry is the API virtual environment's `bin/`, so `deepfix` is the same code revision as the API. The product database path, the API-private directory, and session secrets are not in the environment or the prompt. No `uv tool install` is needed.
- [ ] When `MLFLOW_TRACKING_URI` is an HTTP URL and `MLFLOW_EXPERIMENT_ID` is set, each run's conversation is traced to that MLflow experiment, tagged with the run id. The `deepfix` commands the agent runs do not inherit the OTLP settings.
- [ ] The agent's terminal tool is enabled. Indexing, eval, and config mutation happen only when the agent shells out to `deepfix`. There is no agent server, no second container, and no internal token.
- [ ] The agent may write candidate configs and candidate indexes only under the workspace runs directory for that run id.
- [ ] A test, skipped only when the OpenHands interpreter is not configured or cannot import the SDK, runs one agent turn whose prompt is to execute `deepfix --help` and asserts the command's usage text appears in the final response.
- [ ] A test starts a runner that sleeps past a short timeout and asserts the child process is gone afterwards.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

# 13 — Embedded Hermes agent runner

**What to build:** The API process can run Hermes through its Python library, one new agent per optimization run, without calling the Hermes gateway. The agent works inside the workspace, shells out to a `deepfix` CLI installed from the same git revision as the API, and can write candidate configs and indexes only under that run's directory. Promotion stays in API code after the conversation returns, and the holdout path is never in the prompt or the agent's environment.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] The API server process can import `AIAgent` from `run_agent`. A pinned Hermes commit is recorded in `tasks/notes-hermes-integration.md`, along with the checkout path used in this environment.
- [ ] A domain helper builds one new agent per optimization run with quiet mode, memory skipped, a maximum of 15 iterations, and the API key, base URL, and model taken from the existing LLM settings. It runs one conversation for that run id and returns the final response. It does not call the Hermes gateway on ports 8642 or 9119.
- [ ] The agent's terminal tool is enabled. Indexing, eval, and config mutation happen only when the agent shells out to the `deepfix` CLI. The CLI is installed with `uv tool install` of this repo, pinned to the same git revision as the API, so `deepfix` is on `PATH` and `deepfix --help` succeeds before a run starts. The agent does not import the domain package.
- [ ] The helper runs the agent with the workspace as its working directory. The holdout directory is not in the prompt and not in the environment passed to the agent.
- [ ] The agent may write candidate configs and candidate indexes only under the workspace runs directory for that run id. There is no second Hermes container and no internal token. The Compose Hermes gateway is not on the optimization path.
- [ ] A test, skipped only when the agent library cannot be imported, runs one agent turn whose prompt is to execute `deepfix --help` and asserts the command's usage text appears in the final response.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

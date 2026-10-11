# 14 — Optimization run lifecycle

**What to build:** Every optimization run, scheduled or manual, starts through one function in the API process. That function exports a fresh feedback snapshot, skips the run when there is not enough labeled data, refuses to start a second run while one is active, and otherwise records a running run and launches one Hermes child process. The agent is told to follow the optimization-loop skill against the dev dataset only and to finish by writing a structured result file. The API owns the run record, including killing and failing a run that exceeds its timeout and failing runs interrupted by a restart.

**Reference:** tasks/prd-product-ui.md, `### US-014: Optimization run lifecycle owned by the API`

**Blocked by:**

- 12 — Feedback evaluation dataset and admin summary
- 13 — Hermes agent runner in a child process

**Status:** ready-for-agent

- [ ] Gate thresholds are server settings: minimum dev questions (default 50), minimum new dev questions since the last non-skipped run (default 10), minimum holdout questions (default 15), minimum primary delta (default 0.025), and run timeout in hours (default 3).
- [ ] Starting a run is one Python function in the API process, used by both the scheduler and the admin run-now route. It exports a fresh feedback snapshot and then decides whether to run.
- [ ] If dev, new dev, or holdout questions are below their minimums, it records a run with status skipped and the counts, and does not start the agent.
- [ ] If another run is running, the admin route returns 409.
- [ ] Otherwise it records a running run and starts one background task that launches the runner with the fixed optimization prompt, the dev dataset path, the champion config path, the champion index directory, and the corpus path.
- [ ] The prompt tells the agent to follow the optimization-loop skill by running `deepfix` with JSON output, to use only dense, hybrid, or bm25 retrieval, to write candidates under that run's directory, and to finish by writing `runs/<run_id>/result.json` with the candidate config path (or null), the primary metric, a summary, and the MLflow run id (or null). It does not include anything about holdout.
- [ ] When the runner exits, the API validates `result.json` against a Pydantic model. A missing or invalid file marks the run failed with the runner error. A null candidate marks the run succeeded with the summary as reason. The API does not parse the agent's final response to decide the outcome.
- [ ] A run that exceeds the timeout is killed and marked failed with reason "Timed out", which unblocks the next run. On API startup, any run still marked running is marked failed with reason "Interrupted by restart".
- [ ] Integration tests exercise skip and conflict by calling the lifecycle functions with temporary workspaces and the sample corpus in retrieval-only mode. Those tests do not start the agent. No mocks of the pipeline or eval runner.

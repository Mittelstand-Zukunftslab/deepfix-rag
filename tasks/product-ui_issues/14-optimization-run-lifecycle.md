# 14 — Optimization run lifecycle

**What to build:** Every optimization run, scheduled or manual, starts through one function in the API process. That function exports a fresh feedback snapshot, skips the run when there is not enough new labeled data, refuses to start a second run while one is active, and otherwise records a running run and starts one background agent. The agent is told to follow the optimization-loop skill against the dev dataset only. The API owns the run record, including marking a run failed if it is still running after three hours.

**Blocked by:**

- 12 — Feedback evaluation dataset and admin summary
- 13 — Embedded Hermes agent runner

**Status:** ready-for-agent

- [ ] Starting a run is one Python function in the API process, used by both the scheduler and the admin run-now route. It exports a fresh feedback snapshot and then decides whether to run.
- [ ] If dev has fewer than 20 questions, or fewer than 10 dev questions were added since the last run that was not skipped, it records a run with status skipped and the counts, and does not construct an agent.
- [ ] If another run is running, the admin route returns 409.
- [ ] Otherwise it records a running run and starts one background task. The task builds a new agent and runs a conversation with the fixed optimization prompt, the dev dataset path, the champion config path, the champion index directory, and the corpus path.
- [ ] The prompt tells the agent to follow the optimization-loop skill by running `deepfix` with JSON output, and to write candidates under that run's directory. It does not include the holdout path.
- [ ] If the agent finishes without writing a candidate, the API marks the run succeeded when the final response reports no change, or failed with the agent error text otherwise. The MLflow run id from the agent's eval output is stored when present.
- [ ] A run still running 3 hours after it started is marked failed with reason "Timed out", which unblocks the next run.
- [ ] Integration tests exercise skip and conflict by calling the lifecycle functions with temporary workspaces and the sample corpus in retrieval-only mode. Those tests do not construct an agent. No mocks of the pipeline or eval runner.

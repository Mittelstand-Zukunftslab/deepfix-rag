# Hermes integration

The optimization agent runs through the Hermes Python library in a child process. It does not use the Hermes gateway (ports 8642 and 9119), a second Hermes container, or `DEEPFIX_INTERNAL_TOKEN`. The Compose `hermes` service remains the gateway and is not on this path.

## Pinned checkout

| | |
| :--- | :--- |
| Repository | https://github.com/NousResearch/hermes-agent.git |
| Commit | `ea45ec3936720abef431439152e75b2fc7dd4039` |
| Checkout | `/home/ubuntu/hermes-agent` |
| Interpreter (`HERMES_PYTHON`) | `/home/ubuntu/hermes-agent/.venv/bin/python` |
| Python | 3.14.8 |

That interpreter imports `AIAgent` from `run_agent`. `deepfix_rag` is not installed in the environment.

Create it with:

```bash
git clone https://github.com/NousResearch/hermes-agent.git /home/ubuntu/hermes-agent
git -C /home/ubuntu/hermes-agent checkout ea45ec3936720abef431439152e75b2fc7dd4039
cd /home/ubuntu/hermes-agent
uv python install 3.14
uv sync
```

Set `HERMES_PYTHON` to the interpreter above.

## OpenRouter credentials

The child takes its OpenRouter API key from `DEEPFIX_LLM_API_KEY`. The base URL is `DEEPFIX_LLM_BASE_URL` (default `https://openrouter.ai/api/v1`) and the model is `DEEPFIX_LLM_MODEL_NAME`. The product `LLM_API_KEY` is not copied into the child. The runner also sets `OPENROUTER_API_KEY` to that same key so Hermes sends it to OpenRouter.

The rest of the child environment is `MLFLOW_TRACKING_URI`, `DEEPFIX_SERVER_URL`, and a `PATH` whose first entry is the API virtualenv `bin/`, so `deepfix` is the same revision as the API. The product database path, the API-private directory, and session secrets are not in the environment or the prompt.

## Process boundary

Each run writes `runs/<run_id>/job.json`, starts `scripts/hermes_runner.py` with the workspace as its working directory, and waits. The default timeout is `OPTIMIZATION_RUN_TIMEOUT_HOURS` (3). On timeout the API kills the child's process group.

The runner builds one agent with `quiet_mode=True`, `skip_memory=True`, `max_iterations` from the job (default 15), and the terminal toolset enabled. Hermes state for the run is kept under `runs/<run_id>/.hermes`. Candidate configs and indexes are written only under that run directory. Indexing, eval, and config changes happen when the agent shells out to `deepfix`.

Outside Compose the child is the same OS user as the API. It is never told the API-private directory and it cannot promote itself. In Compose, run the child as a separate user that can write the workspace and cannot read the API-private directory.

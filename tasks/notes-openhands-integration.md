# OpenHands integration

The optimization agent runs through the [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk) in a child process. The API starts one child per run, watches it, and can kill it. It does not use an agent server, a second container, or `DEEPFIX_INTERNAL_TOKEN`.

## Pinned environment

| | |
| :--- | :--- |
| Packages | `openhands-sdk==1.54.0`, `openhands-tools==1.54.0` (always the same version) |
| Virtual environment | `/home/ubuntu/openhands-agent/.venv` |
| Interpreter (`OPENHANDS_PYTHON`) | `/home/ubuntu/openhands-agent/.venv/bin/python` |
| Python | 3.12 |

That interpreter imports `openhands.sdk`, `openhands.tools.terminal`, and `openhands.tools.file_editor`. `deepfix_rag` is not installed in the environment.

Create it with:

```bash
uv venv --python 3.12 /home/ubuntu/openhands-agent/.venv
uv pip install --python /home/ubuntu/openhands-agent/.venv/bin/python \
  openhands-sdk==1.54.0 openhands-tools==1.54.0
```

Set `OPENHANDS_PYTHON` to the interpreter above. Without it, the Optimization screen shows the schedule and history and disables Run now.

## OpenRouter credentials

The API writes the OpenRouter key from `DEEPFIX_LLM_API_KEY` into the run's `job.json`. The base URL is `DEEPFIX_LLM_BASE_URL` (default `https://openrouter.ai/api/v1`) and the model is `DEEPFIX_LLM_MODEL_NAME`. The runner adds the `openrouter/` prefix that LiteLLM needs to route an OpenRouter model, so `openai/gpt-6-luna-pro` becomes `openrouter/openai/gpt-6-luna-pro`. The product `LLM_API_KEY` is not copied into the child.

The rest of the child environment is `DEEPFIX_LLM_*`, `MLFLOW_TRACKING_URI`, `DEEPFIX_SERVER_URL`, `NO_COLOR=1`, `MLFLOW_DISABLE_AGENT_HINT=1`, and a `PATH` whose first entry is the API virtualenv `bin/`, so `deepfix` is the same revision as the API. The product database path, the API-private directory, and session secrets are not in the environment or the prompt.

## MLflow traces

OpenHands traces through OpenTelemetry, and MLflow accepts OTLP traces at `<tracking URI>/v1/traces`. When `MLFLOW_TRACKING_URI` is an `http(s)` URL and `MLFLOW_EXPERIMENT_ID` is set, the API passes `DEEPFIX_AGENT_OTLP_ENDPOINT` and `DEEPFIX_AGENT_OTLP_HEADERS` (`x-mlflow-experiment-id=<id>`) to the child. The runner exports them as `OTEL_EXPORTER_OTLP_*` before importing the SDK, which starts the tracer, and removes them before the terminal tool starts. The `deepfix` commands the agent runs therefore keep logging to their usual experiment.

Each run is one trace. The root span is `deepfix_optimization`, with `agent.step`, `llm.*`, and tool spans under it. The session id is a UUID derived from the run id, and the trace metadata includes `deepfix.run_id`.

## Process boundary

Each run writes `runs/<run_id>/job.json`, starts `scripts/openhands_runner.py` with the workspace as its working directory, and waits. The default timeout is `OPTIMIZATION_RUN_TIMEOUT_HOURS` (3). On timeout the API kills the child's process group.

The runner builds one `Agent` with the terminal tool (subprocess backend) and the file editor, and runs one `Conversation` with `max_iteration_per_run` from the job (default 15). The system message tells the agent that no human is watching and that it must end with the finish action. If the agent stops with a plain message instead, the runner replies "Please continue…" at most twice, as the SDK FAQ recommends. The finish message is the final response. Conversation state is kept under `runs/<run_id>/.openhands`, and token usage is written to `runs/<run_id>/agent.log`. The runner writes `runs/<run_id>/agent_output.json` with the final response, or with the error when the conversation ends in any status other than `finished`. Candidate configs and indexes are written only under that run directory. Indexing, eval, and config changes happen when the agent shells out to `deepfix`.

Outside Compose the child is the same OS user as the API. It is never told the API-private directory and it cannot promote itself. In Compose, run the child as a separate user that can write the workspace and cannot read the API-private directory. The SDK's Docker workspace is the stronger sandbox if that is needed later.

---
name: diagnostic-server
description: >
  Use when deepfix run-diagnostic exits 2 because the DeepFix analysis server is
  not running, or when DEEPFIX_SERVER_URL is unset. Install deepfix-server in an
  isolated uv tool environment, launch it, then call run-diagnostic --json.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
  - llm_reasoning_failure
  - deflection_failure
trigger_conditions:
  - "deepfix run-diagnostic exits 2 with a connection error"
  - "DEEPFIX_SERVER_URL is unset or http://localhost:8844 is not accepting connections"
  - "An optimization iteration needs the analysis server before reading a pathology skill"
cli_actions:
  - "uv tool install git+https://github.com/delcaux-labs/deepfix.git#subdirectory=packages/deepfix-server"
  - "deepfix-server version"
  - "deepfix-server launch -host 0.0.0.0 -port 8844"
  - "uv run deepfix run-diagnostic --config configs/champion.yaml --server-url http://localhost:8844 --limit 30 --json"
---

`deepfix run-diagnostic` does not start the analysis server. It is a client of the separate `deepfix-server` package. Install that server once, leave it running, then call the client from the repo root.

Per-case labels `retrieval_miss`, `retrieval_induced_error`, `llm_reasoning_failure`, and `deflection_failure` still come from `deepfix eval` rows. This server adds the multi-agent retrieval analysis on top of that. Follow `optimization-loop` for the promote-or-revert gate.

## When not to use

Skip the server when `eval --json` already names a pathology skill and you only need `delta` from `eval --compare`. Do not install the server into the project virtualenv with `uv sync`; that mixes it with the RAG package.

If `.env` already sets `DEEPFIX_SERVER_URL` to a reachable host (the template uses `https://deepfix-server.mittelstand-zukunftslab.de`), call `run-diagnostic` against that URL and do not launch a local server.

## Procedure

1. Install the server CLI into uv's tool environment, isolated from this repo.

```
uv tool install "git+https://github.com/delcaux-labs/deepfix.git#subdirectory=packages/deepfix-server"
```

2. Confirm the executable is on `PATH`. If the shell cannot find it, run `uv tool update-shell` and open a new terminal.

```
deepfix-server version
```

3. Configure the server's own model settings. It does not read this repo's `LLM_API_KEY` or `LLM_MODEL`. It reads `DEEPFIX_LLM_*` from the process environment or from a `.env` file in the directory where you launch it. The server CLI has no env-file flag. Export the variables, or launch from a directory whose `.env` contains:

```
DEEPFIX_LLM_API_KEY=
DEEPFIX_LLM_BASE_URL=https://openrouter.ai/api/v1
DEEPFIX_LLM_MODEL_NAME=qwen/qwen3.8-flash
DEEPFIX_LLM_TEMPERATURE=0.7
DEEPFIX_LLM_MAX_TOKENS=8000
DEEPFIX_LLM_CACHE=1
DEEPFIX_LLM_TRACK_USAGE=1
DEEPFIX_SEARCH_PROVIDER=duckduckgo
NUM_REASONING_CHAINS=1
DEEPFIX_SERVER_DATABASE_URL=sqlite:///./deepfix_server.db
MLFLOW_TRACKING_URI=
MLFLOW_EXP_NAME=Deepfix-server
```

Do not commit API keys. Do not assume the RAG `.env` in the repo root satisfies these names.

4. Start the server and leave the process running. Flags are single-dash.

```
deepfix-server launch -host 0.0.0.0 -port 8844
```

5. Point the RAG client at it. The client default is already `http://localhost:8844` (`DEEPFIX_SERVER_URL` in `.env` overrides that). `run-diagnostic` does not send `DEEPFIX_API_KEY`.

6. Run a small diagnosis. `--json` prints the payload on stdout and moves logs to stderr. Pass `--server-url` when you are not using the default.

```
uv run deepfix run-diagnostic --config configs/champion.yaml --server-url http://localhost:8844 --limit 30 --json
```

Exit 0 with a JSON body means the server accepted the dataset. Then read `optimization-loop` and choose a pathology skill. The client waits up to 360 seconds for the server.

Upgrade or remove the tool with `uv tool upgrade deepfix-server` and `uv tool uninstall deepfix-server`.

## Valid values

`run-diagnostic` accepts only:

- `--dataset` / `-d`: path or preset. Overrides `config.data.path_or_repo`.
- `--config` / `-c`: RAG YAML. The file must exist.
- `--server-url`: analysis server URL. Falls back to `DEEPFIX_SERVER_URL`, then `http://localhost:8844`.
- `--limit`: max queries sent to the server.
- `--json`.

There is no `--type` and no `--threshold` flag. `retrieval.top_k` in the YAML is ignored: the diagnostic client hardcodes `top_k=5`.

## Index-time vs query-time

Installing and launching `deepfix-server` does not change the RAG index or the champion config. `data.enable_embedding_pca` and `data.embedding_pca_components` affect only the dataset built for this server, not `deepfix index` or `deepfix eval`.

## Validated findings

No checked-in A/B result for server-side recommendations yet.

## Pitfalls

- Exit 2 on connection refused means the server is down or `DEEPFIX_SERVER_URL` is wrong. Fix the process or the URL. Do not treat it as a failed RAG hypothesis.
- Exit 1 means the config path or dataset is invalid. Fix the command.
- Launching from the repo root loads the RAG `.env`, which uses `LLM_*` names the server does not read. Set `DEEPFIX_LLM_*` explicitly.
- A diagnosis report is not the quality gate. Promote a candidate only from `eval --compare`, as `optimization-loop` describes.

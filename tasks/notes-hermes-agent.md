# Hermes optimization agent

Hermes replaced the OpenHands child in US-010. It is the only optimization
agent: `OPTIMIZATION_AGENT` accepts `hermes` and nothing else.

## Image

- `nousresearch/hermes-agent@sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c`
  (v0.22.7), built as `docker/hermes/Dockerfile` -> image `deepfix-hermes`.
- Runs as the image's non-root `hermes` user. `mem_limit: 4g`, `cpus: "2"`.
- No published host port, no dashboard, no Docker socket.

## Service and URL

- Service `hermes` in `compose.hermes.yml`, included by `docker-compose.yml`.
- Internal URL `http://hermes:8642`, reachable only on the Compose network.
  `HERMES_BASE_URL` defaults to that.
- The API is the only client. The browser never talks to Hermes.

## Required environment

| Variable | Purpose |
| :--- | :--- |
| `API_SERVER_KEY` | Bearer token the API sends to Hermes. At least 16 characters; placeholders are rejected. |
| `HERMES_MODEL` | Model id the sandbox runs. Boot fails without it; there is no code default. |
| `HERMES_MODEL_PROVIDER` | Provider segment, default `openrouter`. |
| `HERMES_MODEL_BASE_URL` | Optional non-default OpenAI-compatible endpoint. |
| `OPENROUTER_API_KEY` | Model calls inside the sandbox. Not the product `LLM_API_KEY`. |
| `HERMES_S3_ACCESS_KEY_ID`, `HERMES_S3_SECRET_ACCESS_KEY` | The `hermes-optimizer` RustFS key, limited to `optimization/runs/*`. |
| `HERMES_CHAT_API_SERVER_KEY` | Key for the stopped chat profile. Optional. |

`LLM_API_KEY`, `RUSTFS_ACCESS_KEY`, and `RUSTFS_SECRET_KEY` must not be present
in the Hermes environment; boot refuses to start when they are.

## The flag

`OPTIMIZATION_AGENT` defaults to `hermes` and is typed `Literal["hermes"]`. A
stale `OPTIMIZATION_AGENT=openhands` in a server env file is a startup
validation error naming the variable rather than a silently ignored value.

## What the API still owns

Holdout, the 0.025 primary-delta rule, promotion, and the champion config. The
sandbox writes `output/result.json` and recommends at most one candidate; it
never promotes.

## Removed with OpenHands

`scripts/openhands_runner.py`, the OpenHands venv in the API image,
`compose.openhands.yml`, the `openhands` service and `openhands_data` volume,
and the `OPENHANDS_PYTHON`, `OH_SESSION_HOST`, `OH_SESSION_API_KEY`,
`OH_SECRET_KEY`, `OPENHANDS_PORT`, and `OPTIMIZATION_GOAL_ROUNDS` variables.

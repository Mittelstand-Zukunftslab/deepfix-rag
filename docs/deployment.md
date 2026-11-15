# Deployment notes: Dokploy

The stack runs as one Compose project on a Dokploy-managed VPS. These notes cover what is specific to that host. Everything else — services, ports, environment variables — is in `docker-compose.yml`, `compose.hermes.yml`, and `example.env`.

## The host is small

The VPS has **7.6 GB of RAM**. The Compose memory caps add up to more than that on purpose: a cap is a ceiling for one container, not a reservation, and not every service is busy at once.

| Service | `mem_limit` | Notes |
| :--- | :--- | :--- |
| `hermes` | 4 GB | Plus `cpus: "2"`. Runs `deepfix index` and `deepfix eval`, which are the memory peaks. |
| `mlflow` | 2 GB | One worker, SQLite backend store. |
| `rustfs` | 1 GB | Object store. |
| `diagnostic-agent` | 1 GB | Optional; idle unless `run-diagnostic` is used. |
| `memgraph`, `falkordb` | 1 GB each | Optional; only for manual graph retrieval. |
| `backend-api`, `web` | none | Small and steady. |

Do not raise `hermes`'s limits to make a round faster or a chat turn survive. If memory is tight, lower the chat's appetite first (`ADMIN_CHAT_MAX_CONCURRENT_TURNS`, then `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` and `ADMIN_CHAT_MAX_PROMPT_CHARS`), and remember that only one optimization run exists at a time.

## Auto-deploy stays off

Keep Dokploy's automatic deploy on push disabled. A full-stack redeploy stops and rebuilds every service, and the pull, build, and container-start burst is what exhausts 7.6 GB. Deploy by hand instead.

## Rebuild one service at a time

Use `--no-deps` so Compose does not restart the services the target depends on:

```bash
docker compose up -d --no-deps --build backend-api
docker compose up -d --no-deps --build hermes
docker compose up -d --no-deps --build web
```

| Changed | Rebuild |
| :--- | :--- |
| `src/deepfix_rag/**`, `configs/**`, `Dockerfile`, `pyproject.toml`, `uv.lock` | `backend-api` |
| `docker/hermes/**`, `.agents/skills/**`, `compose.hermes.yml` | `hermes` |
| `web/**` | `web` |
| `MLFLOW_SERVER_*`, MLflow image or command | `mlflow` |
| RustFS settings | `rustfs` |

`.agents/skills/**` is baked into the Hermes image, so a skill edit needs a Hermes rebuild. The backend image bakes `configs/` and `scripts/` too, so a config change needs a backend rebuild.

A rebuild is also the way a Compose-level environment change takes effect: `docker compose up -d --no-deps <service>` recreates that one container with the new environment.

## Order of operations for a release that touches several services

1. Rebuild `backend-api` and confirm `GET /health` answers.
2. Rebuild `hermes` and confirm `GET http://hermes:8642/health` answers from inside the network.
3. Rebuild `web` last, because it depends on the API being healthy.
4. Confirm the admin section loads, the schedule shows a `next_run_at`, and the champion diff renders.

Run one optimization round after a release that touched the sandbox: it is the only check that exercises the bundle upload, the Hermes run, the result download, and the promotion path together.

## Secrets

Every key in the "Environment variables" table of `README.md` is set as a Dokploy environment variable on the project. They are never committed and never copied into a document, a ticket, or a log. `.env` in the repository is for local development only.

Two keys are required for the stack to boot at all: `API_SERVER_KEY` and `HERMES_MODEL` (`compose.hermes.yml` fails on a missing value), and `HERMES_S3_ACCESS_KEY_ID` / `HERMES_S3_SECRET_ACCESS_KEY` for the run bundle. `OPENROUTER_API_KEY` is required for the sandbox's model calls and for reranking: `backend-api` receives it so the rerank call authorizes with it, and `COHERE_API_KEY` is only the fallback the client reads when it is empty. A deployment that has been reranking with `COHERE_API_KEY` alone can set `OPENROUTER_API_KEY` and rebuild `backend-api` and `hermes`; both take it from the same variable.

## Domains

- The web app has one public domain on `web:8080`.
- The API has one public domain on `backend-api:8000` only if a browser or an external tool must reach it directly. The web app proxies `/api/*` itself, so the common deployment does not need one.
- MLflow keeps **one** domain with a Traefik `basicAuth` middleware, and that hostname must stay in `MLFLOW_SERVER_ALLOWED_HOSTS` with its origin in `MLFLOW_SERVER_CORS_ALLOWED_ORIGINS`. See "MLflow and RustFS lockdown" in `README.md`.
- RustFS has no domain. The console stays off.

Never put a real hostname, IP address, or credential in a document in this repository.

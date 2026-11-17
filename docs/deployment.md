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
| `memgraph`, `falkordb` | 1 GB each | Optional. `falkordb` is required only if a promoted champion uses `graph_hybrid`; `memgraph` only for the manual `graph` backend. |
| `backend-api`, `web` | none | Small and steady. |

Do not raise `hermes`'s limits to make a round faster or a chat turn survive. If memory is tight, lower the chat's appetite first (`ADMIN_CHAT_MAX_CONCURRENT_TURNS`, then `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` and `ADMIN_CHAT_MAX_PROMPT_CHARS`), and remember that only one optimization run exists at a time.

## Auto-deploy stays off

Keep Dokploy's automatic deploy on push disabled. A full-stack redeploy stops and rebuilds every service, and the pull, build, and container-start burst is what exhausts 7.6 GB. Deploy by hand instead.

Auto Deploy must stay off for a second reason now that routing lives in the compose file: a push-triggered redeploy rewrites every container at once, and a label mistake then takes all three sites down together rather than one at a time.

## Rebuild one service at a time

Use `--no-deps` so Compose does not restart the services the target depends on. This is also safe for routing: each service's routers and its `dokploy-network` membership are labels in `docker-compose.yml`, so rebuilding one service recreates it with its own routers and never depends on another service being recreated first.

```bash
docker compose up -d --no-deps --build backend-api
docker compose up -d --no-deps --build hermes
docker compose up -d --no-deps --build web
```

| Changed | Rebuild |
| :--- | :--- |
| `src/deepfix_rag/**`, `configs/**`, `Dockerfile`, `pyproject.toml`, `uv.lock` | `backend-api` |
| `docker/hermes/**`, `.agents/skills/**`, `compose.hermes.yml`, `configs/hpo_retrieval_small.yaml` | `hermes` |
| `web/**` | `web` |
| `MLFLOW_SERVER_*`, MLflow image or command | `mlflow` |
| `traefik.*` labels, `DEEPFIX_WEB_HOST`, `DEEPFIX_API_HOST` | the service whose labels changed |
| `MLFLOW_PUBLIC_HOST`, `MLFLOW_BASIC_AUTH_USERS` | `mlflow` |
| RustFS settings | `rustfs` |

`.agents/skills/**` is baked into the Hermes image, so a skill edit needs a Hermes rebuild. The backend image bakes `configs/` and `scripts/` too, so a config change needs a backend rebuild.

A rebuild is also the way a Compose-level environment change takes effect: `docker compose up -d --no-deps <service>` recreates that one container with the new environment.

## Query expansion inside the sandbox

A round may now sample `retrieval.query_expansion_num` 1 to 3. The completion that rewrites a question goes to `LLM_MODEL` on `LLM_BASE_URL`, which is the sandbox's generation model and not a separate one:

- `compose.hermes.yml` passes `LLM_MODEL` and `LLM_JUDGE_MODEL` to the container. Boot writes them, with `LLM_BASE_URL` and the other model ids, to `/opt/deepfix/eval-env` (root-owned, mode 0644). That file holds model ids and endpoint URLs only.
- The `deepfix` shim reads that file and puts the values into the CLI child's environment, so nothing appears on the command line. It also fills `LLM_API_KEY` from `OPENROUTER_API_KEY` when `LLM_BASE_URL` is OpenRouter's, exactly as it already does for the rerank and embedding slots.
- `LLM_API_KEY` is still rejected in the container environment, and the run bundle still carries no credential: the key stays in the sealed profile `.env` the shim reads.

So a deploy that enables expansion needs the Hermes image rebuilt (new `src/`, new `configs/hpo_retrieval_small.yaml`, new skills). It needs a `backend-api` rebuild too, because the API re-scores a candidate and its champion through the same expansion cache during promotion, and that code lives in `src/deepfix_rag/promotion.py`. No new environment variable is required: the model is the `LLM_MODEL` the deployment already sets. Confirm `LLM_MODEL` is non-empty on the `hermes` service, because a sweep that can expand now exits 1 with `error_type` `missing_model` before the first trial instead of erroring trials one by one.

## Order of operations for a release that touches several services

1. Rebuild `backend-api` and confirm `GET /health` answers.
2. Rebuild `hermes` and confirm `GET http://hermes:8642/health` answers from inside the network.
3. Rebuild `web` last, because it depends on the API being healthy.
4. Confirm the admin section loads, the schedule shows a `next_run_at`, and the champion diff renders.

Run one optimization round after a release that touched the sandbox: it is the only check that exercises the bundle upload, the Hermes run, the result download, and the promotion path together.

## Secrets

Every key in the "Environment variables" table of `README.md` is set as a Dokploy environment variable on the project. They are never committed and never copied into a document, a ticket, or a log. `.env` in the repository is for local development only.

Three keys are required for the stack to boot at all: `API_SERVER_KEY` and `HERMES_MODEL` (`compose.hermes.yml` fails on a missing value), and `HERMES_S3_ACCESS_KEY_ID` / `HERMES_S3_SECRET_ACCESS_KEY` are required as a pair for the run bundle. `BETTER_AUTH_SECRET` is separately required for sign-in: without it every `/api/auth/*` route answers 500. `OPENROUTER_API_KEY` is required for the sandbox's model calls and for reranking: `backend-api` receives it so the rerank call authorizes with it, and `COHERE_API_KEY` is only the fallback the client reads when it is empty. A deployment that has been reranking with `COHERE_API_KEY` alone can set `OPENROUTER_API_KEY` and rebuild `backend-api` and `hermes`; both take it from the same variable.

## Routing and domains

**Routing is defined in `docker-compose.yml`; Dokploy holds no Domain entries for this stack.** Each of `web`, `backend-api`, and `mlflow` joins `dokploy-network`, sets `traefik.docker.network=dokploy-network`, declares an http router on the `web` entrypoint with `redirect-to-https@file`, and declares an https router on `websecure` with `tls.certresolver=letsencrypt`. Each also names its backend with `traefik.http.services.<name>.loadbalancer.server.port`. Nothing publishes a host port, so a single-service `docker compose up -d --no-deps <service>` from the plain repo file keeps the sites up.

| Service | Host variable | https router | Port |
| :--- | :--- | :--- | :--- |
| `web` | `DEEPFIX_WEB_HOST` | `deepfix-web-tls` | `8080` |
| `backend-api` | `DEEPFIX_API_HOST` | `deepfix-api-tls` | `8000` |
| `mlflow` | `MLFLOW_PUBLIC_HOST` | `deepfix-mlflow-tls`, plus `deepfix-mlflow-auth` | `5000` |

- **Delete the Dokploy Domain entry for `web`, `backend-api`, and `mlflow`.** A leftover entry injects a second router (`digitalizationhub-deepfixrag-hvi1a8-N-web` and `-websecure`) for the same `Host`. Traefik picks one, so the result is either an unauthenticated MLflow router or a stale port. The compose file is now the only source.
- **An unset host fails closed.** Each router rule falls back to a `*.invalid` name that matches no request, so a missing variable darkens the site instead of publishing it on a guessed hostname. Set all three host variables before deploying.
- **Router names are `deepfix-*`**, which cannot collide with Dokploy's `digitalizationhub-deepfixrag-hvi1a8-*` names. Keep that prefix if you add one.
- **`dokploy-network` must exist.** Compose declares it `external`, and it is created by Dokploy. On any other host, run `docker network create dokploy-network` once.
- **`MLFLOW_BASIC_AUTH_USERS` doubles every `$`.** The server's docker-compose v1 re-interpolates `.env`, so `htpasswd -nbB admin 'pass' | sed -e 's/\$/\$\$/g'` is the required form, in Dokploy's Environment tab as well. A single-`$` hash resolves corrupted and every login fails.
- **Do not change `WEB_ORIGIN`, `BETTER_AUTH_URL`, or `API_INTERNAL_URL` to the public API host.** The web server reaches FastAPI only over `API_INTERNAL_URL` (`http://backend-api:8000`) and the browser calls the web app's own `/api/*` proxy; no web code reads `DEEPFIX_API_HOST`. `WEB_ORIGIN` stays the public web origin (`https://` + `DEEPFIX_WEB_HOST`) because Better Auth uses it as the token issuer and audience.
- The API's public host is only needed if a browser or an external tool must reach it directly. The web app proxies `/api/*` itself, so the common deployment can leave `DEEPFIX_API_HOST` at its fail-closed default and the `deepfix-api` routers simply match nothing.
- RustFS has no domain. The console stays off.

Two ports still publish, and both are optional debug surfaces rather than part of the request path: `diagnostic-agent` on `8844`, so `deepfix run-diagnostic` can be pointed at it from outside the network, and `memgraph` / `falkordb` on `7687` and `6379` from `compose.graphdb.yml` (needed only when a champion uses `graph_hybrid` or a manual run uses `graph`). Remove the mapping where nothing outside the host uses it; the Compose-network address (`http://diagnostic-agent:8844`, the default `DEEPFIX_SERVER_URL`) keeps working.

Never put a real hostname, IP address, or credential in a document in this repository.

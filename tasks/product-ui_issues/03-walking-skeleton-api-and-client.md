# 03 — Walking skeleton: FastAPI shell, generated client, and TanStack Start app

**What to build:** A FastAPI app that boots beside the existing package, and a TanStack Start app whose TypeScript client is generated from that app's OpenAPI schema. A signed-out page loads and shows the health status by calling the generated SDK. This proves the contract path end to end before any product feature is added.

**Blocked by:** None — can start immediately.

**Status:** done (PR #4). The browser base URL moves to `/api` in ticket 04.

- [x] The API starts with `uv run --env-file .env uvicorn deepfix_rag.api.app:app`.
- [x] `GET /health` returns HTTP 200 and `{"status": "ok"}` without calling the LLM, embeddings, MLflow, or Hermes.
- [x] Route handlers parse input, call domain code, and serialize output. No retrieval, indexing, or evaluation logic lives in route functions.
- [x] Errors use `{"detail": "<message>"}` with 400 for bad input, 401 for no session, 403 for wrong role, 404 for unknown ids, 409 for conflicting state, and 503 when a required dependency is unavailable. That body is a Pydantic model named in the route's responses, so it appears in the schema.
- [x] Every public route declares a Pydantic request body (or form fields) and a Pydantic response model. No public route returns an untyped dict.
- [x] Every public route sets an explicit snake_case operation id. FastAPI must not invent ids from function names.
- [x] The app has no `/internal` routes. `GET /openapi.json` lists only the public product routes, and a test asserts no path starts with `/internal`.
- [x] `uv run python -m deepfix_rag.api.export_openapi` writes the schema to `web/openapi.json` by calling the app's OpenAPI method. It does not start a server and does not load an index.
- [x] An `api` pytest marker is registered and used for all API tests.
- [x] `web/openapi-ts.config.ts` uses `@hey-api/openapi-ts` with input `./openapi.json` and output `./src/client`, and plugins `@hey-api/typescript`, `@hey-api/sdk`, and `@hey-api/client-fetch`. No second HTTP client plugin is enabled.
- [x] The generated fetch client is configured once, in `web/src/api.ts`, with the API base URL from the environment and credentials included so the session cookie is sent.
- [x] `npm run generate:api` in `web/` exports `web/openapi.json` and then runs the generator. `web/src/client` is produced only by that command. Nobody edits files in that directory by hand.
- [x] CI regenerates the client and fails if `web/openapi.json` or `web/src/client` differs from what is committed.
- [x] Application code under `web/src` imports SDK functions from the generated client. A check fails if any file outside the generated client calls `fetch` against the API origin.
- [x] The generated SDK contains no function whose path starts with `/internal`.
- [x] A page in the TanStack Start app displays the health status returned by the generated SDK.
- [x] `uv run ruff check` and `uv run ruff format --check` pass, and the web typecheck passes.

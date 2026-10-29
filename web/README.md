# DeepFix web app

TanStack Start app for the DeepFix product UI (see `../tasks/prd-product-ui.md`). It talks to the
FastAPI service in `../src/deepfix_rag/api/` only through a TypeScript client generated from
FastAPI's OpenAPI schema.

## Accounts and access

The web server owns sign-in. [Better Auth](https://www.better-auth.com) stores users and sessions
in SQLite (`AUTH_DATABASE_PATH`, default `~/.deepfix/web/auth.db`) and creates its tables on first
use. Roles are `member` (every registration) and `admin`.

- `/api/auth/*` is handled by Better Auth in `src/server.ts` and never reaches FastAPI.
- Every other `/api/*` call is proxied to FastAPI with the browser's cookies removed and a
  15-minute Ed25519 token (`Authorization: Bearer …`) naming the user, email, and role. FastAPI
  checks it against `/api/auth/jwks` and keeps its own admin check as a backstop.
- The optimization loop is reached only through server functions in
  `src/services/optimization.functions.ts`, guarded by `requireRole("admin")` from
  `src/lib/auth-middleware.ts`.
- Documents are shared by the whole team; searches and chats are private to the user who ran them.

Create the first admin (or promote an existing account) from `web/`. The password is read from
stdin:

```sh
bun run auth:create-admin --email admin@example.com
```

## Development

```sh
# 1. API (from the repository root)
uv run --env-file .env uvicorn deepfix_rag.api.app:app --reload

# 2. Web app (from web/)
cp .env.example .env.local   # set BETTER_AUTH_SECRET
bun install
bun run dev        # http://localhost:8080
```

## Docker

From the repository root, set `BETTER_AUTH_SECRET` in `.env` (see `../example.env`), then:

```sh
docker compose up --build web   # http://localhost:8080
```

`API_INTERNAL_URL` defaults to `http://backend-api:8000`, the FastAPI service in Compose (`docker compose up --build backend-api`, published on the host at http://localhost:8000). `BETTER_AUTH_URL` is `WEB_ORIGIN` and must match. SQLite auth data is stored in the `web_auth` volume (`AUTH_DATABASE_PATH=/data/auth.db`).

Build the image on its own with `docker build -t deepfix-web web`.

## API client

`openapi.json` and `src/client/` are generated. Never edit them by hand.

```sh
bun run generate:api   # exports ../web/openapi.json from FastAPI, then runs openapi-ts
```

Run it after every change to an API route or Pydantic model. CI regenerates both and fails if they
differ from what is committed. The API origin is set once in `src/api.ts`; application code
imports SDK functions from `@/client` and never calls `fetch` itself.

## Checks

```sh
bun run typecheck && bun run lint && bun run test
```

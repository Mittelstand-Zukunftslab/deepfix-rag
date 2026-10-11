# DeepFix web app

TanStack Start app for the DeepFix product UI (see `../tasks/prd-product-ui.md`). It talks to the
FastAPI service in `../src/deepfix_rag/api/` only through a TypeScript client generated from
FastAPI's OpenAPI schema.

## Development

```sh
# 1. API (from the repository root)
uv run --env-file .env uvicorn deepfix_rag.api.app:app --reload

# 2. Web app (from web/)
cp .env.example .env.local
bun install
bun run dev        # http://localhost:8080
```

## API client

`openapi.json` and `src/client/` are generated. Never edit them by hand.

```sh
bun run generate:api   # exports ../web/openapi.json from FastAPI, then runs openapi-ts
```

Run it after every change to an API route or Pydantic model. CI regenerates both and fails if they
differ from what is committed. The API origin and `credentials: 'include'` are set once in
`src/api.ts`; application code imports SDK functions from `@/client` and never calls `fetch`
itself.

## Checks

```sh
bun run typecheck && bun run lint && bun run test
```

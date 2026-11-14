# CHAT-009 — Web data layer for admin chat

## Type
Feature

## Status
Draft

## Summary
A frontend developer writes UI against typed contracts and one SSE reader, so no component hand-writes a request
body, calls `fetch` directly, or parses an event frame twice — and no model id, key, or Hermes host reaches the
browser bundle.

## PRD traceability
- Related: FR-003, FR-022, FR-026 (the `EXEMPT` entry it adds is one of the four named exceptions)
- Journeys: J1 (send a turn and stream it), J3 (stop and retry), J2 (confirm a card)
- NFR touchpoints: NFR-7 (`@assistant-ui/react` stays at `^0.15.28`, no new chat runtime package)

## Dependencies
- Requires: CHAT-003 through CHAT-008 — every route the service calls must exist and be in `web/openapi.json`, so
  generate the client last in that chain. In practice: run `npm run generate:api` after CHAT-008's commit.
- Blocks: CHAT-010 (the page imports this service and these schemas).

## Acceptance criteria

Generated client and exemption:

1. Given `web/openapi.json`, when `npm run generate:api` (`@hey-api/openapi-ts` 0.99.0) runs, then the generated
   client under `web/src/client/` contains all eleven `/admin/chat` operations — nine JSON routes plus the two
   streaming routes — and the TypeScript types for every chat schema; no hand-written `fetch` for a JSON route
   exists anywhere.
2. Given `web/src/test/api-boundary.test.ts`, when this story lands, then its `EXEMPT` set contains
   `join("services", "admin-chat-sse.ts")` — as that relative path, because the test matches with
   `EXEMPT.has(relative(SRC, file))` — and the existing three entries are unchanged.
3. Given `web/src/services/admin-chat-sse.ts`, when its own test reads the file, then the only request target in
   it is a `/api/admin/chat/...` path, and the module is the only place in `web/src` that parses an SSE frame.

Service and server functions:

4. Given `web/src/services/admin-chat-service.ts`, when its exports are listed, then they are exactly
   `threads()`, `thread(threadId)`, `createThread()`, `deleteThread()`,
   `streamTurn({threadId, messages, retryOf, signal, onChunk, onTool, onAction, onDone, onError})`,
   `resumeTurn(turnId, …)`, `confirmAction(actionId)`, `rejectAction(actionId)`, `linkRun(threadId, runId)`, and
   `runs(threadId)`.
5. Given each JSON call, when it executes, then it goes through a server function in
   `web/src/services/admin-chat.functions.ts` guarded by `requireRole("admin", …)` from
   `web/src/lib/auth-middleware.ts`, in the shape of `web/src/services/optimization.functions.ts`.
6. Given `streamTurn`, when it runs, then it posts to `/api/admin/chat/completions` with `credentials: "include"`,
   `Accept: text/event-stream`, and an `AbortController`; it parses `data:` frames, tolerates `: keepalive`
   comments, tolerates a frame split across reads, calls `onDone` exactly once, and surfaces an abort through
   `onError` exactly once.
7. Given a `deepfix.turn` frame, then `onDone` receives the turn id, status, and actions. Given a
   `deepfix.error` frame, then `onError` receives its message. Given a `hermes.tool.progress` event, then `onTool`
   receives its payload.
8. Given a 429 or a 4xx from the JSON calls, when the generated client returns an error, then the service throws an
   `Error` whose message is the API's `detail` string or the nested `detail.message` (CHAT-003's union), and whose
   `code` is exposed when the body carries one, so the UI can branch on `code` rather than on the sentence.

Contracts:

9. Given `web/src/schemas/admin-chat.ts`, when it is read, then it exports Zod schemas for thread, message, turn,
   action, and run summary, and the UI imports its types from there.
10. Given `web/src/test/admin-chat-stream.test.ts`, when it runs, then it drives the parser with three fixtures —
    a normal multi-chunk reply, a reply with a `deepfix.turn` frame carrying an action, and a truncated stream
    followed by an abort — and asserts the accumulated text, the action payload, the single `onDone`, and the
    single abort callback.

Boundary:

11. Given every file under `web/src`, when `web/src/test/admin-chat-boundary.test.ts` scans them (except its own
    file), then none contains `8643`, `HERMES_CHAT`, `OPENROUTER`, `API_SERVER_KEY`, or `hermes:`, and the only chat
    route strings in the app are the same-origin `/api/admin/chat/...` paths.

## Test commands

```bash
cd web
npm run generate:api
bun run typecheck
bun run lint
bun run test src/test/admin-chat-stream.test.ts
bun run test src/test/api-boundary.test.ts
bun run test src/test/admin-chat-boundary.test.ts
bun run test
```

`npm run generate:api` needs `uv` on the PATH because it shells out to `uv run python -m deepfix_rag.api.export_openapi`
from `web/`; run it from a shell where the repository's `.venv` resolves.

## Out of scope
- Any React component, route, or styling (CHAT-010).
- Changes to the Ask screen (`web/src/features/chat/chat-page.tsx`) and its tests.
- A websocket, a second protocol, or a new origin.
- Retries, backoff, or caching of chat responses. The UI offers an explicit retry control; the transport does not.

## Notes
- The generated client cannot stream, which is why `admin-chat-sse.ts` exists and why it is exempt. Keep the
  exemption narrow: the module must contain one `fetch` (or `sendMessage` from the server-function client for the
  POST) and nothing else.
- The server function for the streaming POST must not buffer: TanStack Start's server function returns a
  `Response` whose body is streamed, and `web/src/server.ts` already relays `/api/*` bodies unchanged. Reuse the
  existing proxy path rather than adding a second one — the browser calls `/api/admin/chat/...`, which
  `src/api-proxy.ts` forwards to FastAPI.
- Parse frames by accumulating the raw text buffer and splitting on `\n\n`; a `data:` line may arrive split across
  two reads (a fixture covers this). Never `JSON.parse` a partial line.
- `code` extraction: read `detail.code` when `detail` is an object, else `undefined`. This must agree with
  CHAT-003's `errorDetail` change; a shared helper in `api-result.ts` is the right place if both need it.
- Do not import `@assistant-ui/react` from this layer; it is a transport, not a renderer.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; `bun run test` fully green
- [ ] `web/openapi.json` regenerated and committed with the client
- [ ] `bun run typecheck` and `bun run lint` pass
- [ ] Committed on its own before CHAT-010 starts

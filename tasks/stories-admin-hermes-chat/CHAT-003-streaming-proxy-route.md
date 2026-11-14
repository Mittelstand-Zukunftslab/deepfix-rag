# CHAT-003 — The streaming proxy route

## Type
Feature

## Status
Draft

## Summary
An admin's message reaches the chat Hermes profile through one admin-only deepfix-api route, and its reply streams
back to the browser as SSE without any key or model id ever leaving the server. The route is the only place the
admin identity, the limits, the disabled gate, the error codes, and the redaction meet.

## PRD traceability
- Related: FR-003, FR-004, FR-005, FR-007, FR-019, FR-021, FR-028
- Decisions: D-1 (disabled 503 for Hermes-facing routes, with two exempt routes), D-2 (disabled sentence and
  `code`), D-3 (model fallback and the misconfigured 503)
- Journeys: J1 steps 3–6 (a streamed first answer), J3 (upstream error paths)
- NFR touchpoints: NFR-3 (no added buffering), NFR-4 (admin on every route, check order), NFR-8 (one structured
  log line per turn start and terminal state)

## Dependencies
- Requires: CHAT-001 (settings, gate predicate, base URL, key), CHAT-002 (`admin_chat` module, audit kinds).
- Blocks: CHAT-004 (the prompt is injected here), CHAT-005 (resume and cancel build on this turn lifecycle),
  CHAT-006 (rate limiting wraps this route), CHAT-009 (the client streams against it).

## Acceptance criteria

Route and identity:

1. Given `POST /admin/chat/completions` with `operation_id="admin_chat_completion"`, when the app's router list is
   inspected, then it is registered in `src/deepfix_rag/api/routes/admin_chat.py`, included in `app.py`, and its
   handler calls `require_admin` from `src/deepfix_rag/api/deps.py` and delegates domain work to `admin_chat`.
2. Given an unsigned caller, when the route is called, then 401. Given a member, then 403. Both happen even when
   `HERMES_CHAT_ENABLED` is false.
3. Given a request whose body is `{thread_id, messages: [{role, content}], retry_of}`, when it is validated, then
   `role` accepts only `user` and `assistant`; a `system` or `tool` role is 400 and no upstream call is made.
4. Given a request whose `messages` disagree with what the thread already stored, or whose last element is not a
   `user` message, when it is validated, then 400 with the sentence
   "The conversation is out of date. Reload the thread."
5. Given the request, when the upstream body is built, then `messages` is exactly the server system message plus
   the thread's stored messages in `seq` order plus the request's final user message — never the client's earlier
   elements.

Limits (FR-019):

6. Given `ADMIN_CHAT_MAX_PROMPT_CHARS` (48000), when the total request characters exceed it, then 400 with a
   readable sentence and no stack trace. Given `ADMIN_CHAT_MAX_MESSAGES` (60), when the message count exceeds it,
   then 400 likewise. Both are read from settings, not literals.
7. Given `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` (300), when a turn runs past the deadline, then the route emits
   `{"type": "deepfix.error", "message": "This reply took too long and was stopped."}`, marks the turn `failed`
   with that reason, closes the upstream request, writes `turn_failed`, and keeps the partial text.

Gate and error codes (D-1, D-2, FR-028):

8. Given `HERMES_CHAT_ENABLED=false`, when an admin calls `POST /admin/chat/completions` (the only
   Hermes-facing route that exists at this commit), then the response is 503 with
   `detail: {"code": "chat_disabled", "message": "Hermes chat is turned off on this server."}` and the test's
   upstream stand-in server receives zero requests. The gate is a router dependency, so later stories inherit it
   without touching this file; a test asserts every route currently registered under `/admin/chat` is either
   gated or in `GATE_EXEMPT_PATHS`, which is what keeps the property as the router grows. `GET /admin/chat/audit`
   (200) and `DELETE /admin/chat/threads/{id}` (204) are exercised by CHAT-005 and CHAT-006, which add them, and
   all nine Hermes-facing routes are checked together in CHAT-012.
9. Given `HERMES_CHAT_ENABLED=true` with an empty `HERMES_CHAT_API_SERVER_KEY`, or with both
   `HERMES_CHAT_MODEL` and `HERMES_MODEL` empty, when a Hermes-facing route is called, then 503 with
   `code: "chat_misconfigured"` and a message naming `HERMES_CHAT_API_SERVER_KEY` and
   `HERMES_CHAT_MODEL`/`HERMES_MODEL`, and no upstream call is made. The configuration check is a separate
   dependency from the enabled gate, applied only to Hermes-facing routes, so the exempt routes that CHAT-005 and
   CHAT-006 add never inherit it.
10. Given `ErrorResponse` in `src/deepfix_rag/api/schemas.py` is `detail: str` today, when this story lands, then
    it becomes `detail: str | ChatErrorDetail` with `ChatErrorDetail = {code: str, message: str}`; every existing
    route's OpenAPI schema is unchanged; a test asserts the chat routes document the object form.
11. Given `web/src/services/api-result.ts`, when `errorDetail` receives a `detail` object, then it returns
    `detail.message`; given a string, then it returns the string. A test covers both shapes.
12. Given the identity provider is unreachable, `current_user` still raises 503 with a string detail and no
    `code`, so it is distinguishable from both chat 503s. A test asserts the three bodies differ.

Order of checks:

13. Given a Hermes-facing route, when it is called, then the order is: identity, admin role, `HERMES_CHAT_ENABLED`,
    configuration, work. Given `GET /admin/chat/audit` or `DELETE /admin/chat/threads/{id}`, then the order is:
    identity, admin role, work — no enabled check and no configuration check. At this commit only the completions
    route is Hermes-facing, so the ordering test asserts the two steps it can (401/403 before the 503, and the
    disabled 503 before any upstream contact with a member still getting 403); CHAT-005 and CHAT-006 add the exempt
    routes' own assertions and CHAT-008's route-inventory test pins the full set.

Upstream call (FR-007, FR-003):

14. Given an enabled, configured route, when it forwards, then the upstream request is
    `POST {HERMES_CHAT_BASE_URL}/v1/chat/completions` with `Authorization: Bearer <HERMES_CHAT_API_SERVER_KEY>`,
    `Content-Type: application/json`, `stream: true`, and `model` from `HERMES_CHAT_MODEL` falling back to
    `HERMES_MODEL`. With `HERMES_CHAT_MODEL` unset, the upstream body carries the `HERMES_MODEL` value.
15. Given each upstream `data:` JSON frame, when it is relayed, then the `model` key is deleted and the frame is
    re-serialised; every other key and value is preserved; `: keepalive` comments are forwarded byte for byte; a
    `data:` payload that is not a JSON object is dropped and logged, never forwarded. The test asserts the
    forwarded stream carries no model id while the assistant text, `finish_reason`, and a
    `hermes.tool.progress` event still arrive.
16. Given the response, when it is returned, then it is `StreamingResponse(media_type="text/event-stream")` with
    `Cache-Control: no-cache`, `Connection: keep-alive`, and `X-Accel-Buffering: no`, and the upstream body is not
    buffered: the test server delays its second chunk and the test asserts the first frame reached the client
    before that delay ended.
17. Given `Content-Type: application/json` on the request (non-streaming), then 400.
18. Given no `X-Hermes-Session-Id` header is sent and `admin_chat_turns` has no `hermes_session_id` column, when
    the schema and the request are inspected, then both hold; the proxy is the only source of history.

Turn lifecycle and audit:

19. Given a stream that completes, when it ends, then the assistant text is stored, `first_token_ms` and
    `ended_at` are set, the turn is `completed`, `turn_completed` is appended, and one final SSE frame
    `{"type": "deepfix.turn", "turn_id", "status", "actions": []}` is emitted before closing. `turn_started` was
    written before the upstream call.
20. Given an upstream error before the first token, then 502 with a readable sentence. Given an error after the
    first token, then a `deepfix.error` frame and a close. Neither path includes the upstream body verbatim; both
    run through the redaction helper. A test asserts `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, and
    `OPENROUTER_API_KEY` never appear in an error frame or an audit `detail`.

Tests:

21. Given the test suite, when it runs, then it uses a real local HTTP server standing in for the chat profile (no
    `unittest.mock`) and covers: 401, 403, a reply split across several chunks, a forwarded `: keepalive`,
    upstream 500 before the first token, an error after the first token, an oversized prompt (400), a `system`
    role (400), a history mismatch (400), the exact upstream body (stored history plus the new user message), the
    disabled 503 with zero upstream hits, and the misconfigured 503 with zero upstream hits.
22. Given the OpenAPI document, when `uv run python -m deepfix_rag.api.export_openapi` runs, then the new
    operation id appears and `web/openapi.json` is regenerated by `npm run generate:api` in `web/`.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_routes.py
uv run --env-file .env pytest -v tests/test_api_optimization.py
uv run --env-file .env pytest -v -m api
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck && bun run test src/test/api-boundary.test.ts
```

The upstream stand-in is a real `http.server`/`uvicorn` thread on `127.0.0.1:0` that the test starts and points
`HERMES_CHAT_BASE_URL` at, so streaming and abort behaviour are exercised over TCP, not through a stub object.

## Out of scope
- The system prompt and the digest content (CHAT-004). This story injects a placeholder system message built by
  `admin_chat.system_prompt`, which CHAT-004 fills in; ship this story with the function returning the policy
  text only, and let CHAT-004 add the digest.
- Resume, cancel-on-abort, and retry (CHAT-005).
- Rate limiting (CHAT-006). Until CHAT-006 lands, the route has no limiter; do not add a stubbed one.
- Action-card parsing (CHAT-007); the terminal frame carries `"actions": []` until then.
- The chat UI and its SSE reader (CHAT-009, CHAT-010).

## Notes
- There is no SSE helper in the API today. Put the relay in `src/deepfix_rag/api/routes/admin_chat.py` (or a small
  `src/deepfix_rag/api/streaming.py` if `hermes_client`'s parser can be reused). `hermes_client.py` already has an
  SSE parser with `: keepalive` handling and redaction for the runs API; reuse it rather than writing a second
  parser, and keep `model`-stripping specific to this route.
- Use `httpx.AsyncClient.stream(...)` inside an `async def` handler. Closing the upstream request on client
  disconnect comes from the ASGI receive channel; CHAT-005 makes that a `cancelled` turn, so implement the close
  hook here and the status transition there.
- `HTTPException(status_code=503, detail={"code": ..., "message": ...})` is what FastAPI serialises as a nested
  `detail`. The OpenAPI union in AC 10 keeps the document honest; do not change other routes to objects.
- Constants for the two codes (`chat_disabled`, `chat_misconfigured`) and the exact sentences belong in one place
  in the module so CHAT-010's UI branches and CHAT-012's checks cannot drift. The UI must switch on `code`.
- `ApiModel` sets `json_schema_serialization_defaults_required=True`; adding the union to `ErrorResponse` changes
  the generated TypeScript error type. Regenerate the client in this story (AC 22) and keep
  `web/src/services/api-result.ts`'s existing behaviour for string details.
- Log one structured line per turn start and per terminal state, in the
  `optimization run=… event=…` style (NFR-8): `chat thread=… turn=… event=turn_started|turn_completed|turn_failed`.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; the upstream stand-in is a real server, no mocks
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated: `tasks/prd-admin-hermes-chat.md` is not edited; `example.env` already carries the names from
      CHAT-001
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, `bun run typecheck`, and
      `bun run lint` pass
- [ ] Committed on its own before CHAT-004 starts

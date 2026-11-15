# ASKCHAT-006 — One typed web data layer and one Chat per thread

## Type
Feature

## Status
Draft

## Summary
The Ask page gets the same thin data layer the admin Hermes chat already has: Zod contracts for the frames, one module that reads the event stream, a service over the generated client, and a transport that builds one `Chat` per thread. The page above it holds no protocol knowledge and no stream parser.

## PRD traceability
- Related: FR-029 (the data layer half), FR-031, FR-032
- Decisions: D-1 (the API speaks the AI SDK stream), D-5 (server-side threads the client reads)
- NFR touchpoints: NFR-7 (no second chat runtime; AI SDK 7 and Assistant UI 0.15 as shipped), NFR-4 (no credential in the browser)

## Dependencies
- Requires: ASKCHAT-001 … ASKCHAT-005 — all six routes and the limits exist.
- Blocks: ASKCHAT-007, ASKCHAT-008, ASKCHAT-009.

## Acceptance criteria

**The contracts**

1. Given `web/src/schemas/ask-chat.ts`, when it is read, then it holds Zod schemas for the terminal turn frame (`turn_id`, `status`, `grounding`, `citations`), the grounding data part (`grounding`, `citations`, `max_score`, `cutoff`), and the `search_documents` output (`query`, `grounding`, `passages[]`, `max_score`), mirroring how `web/src/schemas/admin-chat.ts` types the admin chat's frames.
2. Given those schemas, when a frame fails validation, then the page renders the turn without the optional detail rather than throwing, and the failure is logged once — a malformed frame must not blank a conversation.

**The one stream reader**

3. Given `web/src/services/ask-chat-sse.ts`, when it is read, then it is the only module in `web/src` that reads an event stream, and the only paths it names are `/api/ask/chat/...`.
4. Given `web/src/test/api-boundary.test.ts`, when it runs, then its `EXEMPT` set contains `join("services", "ask-chat-sse.ts")` — the relative-path form that test matches on — and the test is otherwise unchanged. A bare filename would silently miss.
5. Given `web/src/services/ask-chat-service.ts`, when it is read, then it wraps the generated client for the five thread routes and delegates the two streaming calls to the reader, so no component calls `fetch` directly.

**The transport**

6. Given `web/src/features/chat/ask-chat-transport.ts`, when it is read, then `createAskChat(threadId, seed, hooks)` returns one `Chat` per thread built with `AssistantChatTransport`, whose `prepareSendMessagesRequest` sends `{thread_id, messages, regenerate}` and whose `prepareReconnectToStreamRequest` targets `/api/ask/chat/turns/{turnId}/stream`. The shape follows `web/src/features/admin-chat/chat-transport.ts`, which already proves it.
7. Given two threads, when each has a `Chat`, then a message streaming in one never appears in the other, because the AI SDK's `Chat` owns its own messages and stream.
8. Given a stored thread loaded from `GET /ask/chat/threads/{thread_id}`, when it is seeded into a `Chat`, then the assistant messages take their `turn_id` as their message id, so a resumed stream replaces a seeded answer instead of landing beside it.

**The required test**

9. Given `web/src/test/ask-chat-stream.test.ts`, when it runs, then frames copied from a real streamed response, driven through the real transport, produce one assistant message with a text part, two `tool-search_documents` parts each with its output, and the grounding data part.
10. Given the same test file, when it runs, then a second `Chat` for another thread does not inherit the first thread's messages, and seeding a stored thread and then reloading draws the answer exactly once.

## Out of scope
- The Ask page itself, its layout, its Stop control, and its thread list. ASKCHAT-007.
- The sources panel, citation links, and the badge. ASKCHAT-008.
- Importing legacy localStorage sessions. ASKCHAT-009.
- Any change to the admin Hermes chat's transport, schemas, or service. The two share a pattern, not a module.
- A hand-written streaming parser for parts the AI SDK already models. The reader exists only for the frames the generated client cannot express.

## Notes
- One `Chat` per thread is what fixed the message-migration bug in the admin chat (CHAT-015): a single shared runtime let a reply in flight land in whichever thread was on screen. ASK-006 fixed the same class of bug for pending state in the current page, and this story keeps that property at the transport layer.
- The generated client cannot consume a stream, which is why one module owns the `fetch` and why it is exempted by relative path. Keep that module free of React and of business logic: it parses frames and calls handlers.
- `errorMessage(error)` should follow the admin chat's shape — pull the server's sentence out of the AI SDK's wrapper message, and fall back to a fixed sentence — so a 429 or a 503 reaches the member as the sentence the API wrote.

## Definition of done
- [ ] Code complete
- [ ] `web/src/test/ask-chat-stream.test.ts` green through the real transport
- [ ] `api-boundary.test.ts` green with the one new exemption
- [ ] `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build` pass
- [ ] Committed on its own before ASKCHAT-007 starts

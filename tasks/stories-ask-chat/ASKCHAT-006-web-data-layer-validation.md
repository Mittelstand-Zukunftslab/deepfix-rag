# Story validation: ASKCHAT-006 — One typed web data layer and one Chat per thread

- Story file: `tasks/stories-ask-chat/ASKCHAT-006-web-data-layer.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. The story is a developer-facing one — "one typed layer, no protocol knowledge in the page" — and every AC names a file, an exported function, a schema field, or a test's observation.

### Should-fix

1. **AC 4 needed the exact `EXEMPT` form.** `web/src/test/api-boundary.test.ts` matches with `EXEMPT.has(relative(SRC, file))`, so a bare `ask-chat-sse.ts` string would silently never match and the test would fail with an unhelpful message. The story now names `join("services", "ask-chat-sse.ts")` — the same form the admin chat's entry uses.
2. **AC 2's failure behaviour was unspecified.** A schema mismatch could reasonably throw, which would blank a conversation over one malformed optional frame. The story now says render without the optional detail and log once.

### Nice-to-have

- The story does not pin the `errorMessage` implementation. It names the shape (pull the server's sentence out of the AI SDK wrapper, else a fixed sentence) because that is what makes a 429 or 503 readable, and leaves the code open.

## Suggested edits (applied)

- AC 4 given the relative-path form.
- AC 2 given the no-blank-screen rule.

## PRD alignment

- FRs: FR-029 (the data-layer half), FR-031 (reuse the patterns rather than invent), FR-032 (one stream reader, one exempt entry).
- Decisions: D-1 (the API speaks the AI SDK stream — this is the client half), D-5 (threads the client reads).
- Gaps: none. FR-029's page half is ASKCHAT-007.

## NFR touchpoints

- **NFR-7** — AC 6: the transport is `AssistantChatTransport` from `@assistant-ui/react-ai-sdk`, so no second chat runtime is introduced.
- **NFR-4** — AC 3: the reader names only `/api/ask/chat/...` paths, so the BFF remains the only way to the API and the browser holds no credential.
- **Accessibility** — not implied: no UI ships in this story.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`206e36c`)
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 4 said `api-boundary.test.ts` changes and everything else stays put | AC 4 allows one further change: `admin-chat-boundary.test.ts`'s "only reader" assertion | That assertion is false the moment a second reader exists. Leaving it would have meant deleting the Ask reader or weakening the test silently |
| 2 | AC 9 expected "two `tool-search_documents` parts each with its output" | AC 9 expects one part per tool call | The AI SDK folds a call's input and output into one `tool-<name>` part, keyed by `toolCallId` |
| 3 | AC 10's "seeding a stored thread and then reloading draws the answer exactly once" was untestable as written | Kept, but implemented against the seeded assistant message id being the `turn_id` | That id is what makes a resumed stream replace the seeded answer rather than landing beside it |

### Blockers

None.

### Should-fix (applied)

1. The Ask reader's exemption needed its own narrow test, as the admin one has.
   `ask-chat-boundary.test.ts` pins its targets to `/api/ask/chat/...` and asserts
   it holds no model id, key, or upstream host.

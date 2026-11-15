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

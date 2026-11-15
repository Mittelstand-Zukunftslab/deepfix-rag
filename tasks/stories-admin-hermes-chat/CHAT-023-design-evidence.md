# CHAT-023 — Prove the redesign against a mocked backend

## Type
Chore

## Status
Draft

## Summary
The redesign is shown, not asserted: screenshots of the empty state, a conversation with markdown, code, and an action card, the sidebar with grouped threads, dark appearance, and a phone width, all captured against a mocked backend so no key, model, or Hermes host is involved.

## PRD traceability
- Related: FR-022, FR-025
- Out-of-PRD: presentation evidence for the redesign (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-017 … CHAT-022.
- Blocks: none.

## Acceptance criteria

1. Given a mocked chat backend — the server functions and the two `/api/admin/chat/` streams answered from a fixture — when the page is driven in a browser, then every screenshot is produced without a real FastAPI process, Hermes container, or model call.
2. Given the capture, when it is reviewed, then it contains exactly these six images: empty state, conversation with markdown and a code block, conversation with a proposed action card, sidebar with threads in at least three date groups, dark appearance, and a 390 px-wide phone layout.
3. Given each image, when it is checked, then the centred column, the composer, and the sidebar are visible where the story says they should be, and no screenshot shows a model id, key, or internal host.
4. Given the story's Definition of done, when the branch is inspected, then no mocking code that exists only for the capture is committed: the fixture lives in the test suite or is removed, and `bun run typecheck`, `bun run lint`, `bun run test`, and `bun run build` pass on the committed tree.
5. Given the image and layout decisions, when the report is written, then it lists what LibreChat shows that this page does not, and why each difference exists.

## Out of scope
- Deployed verification (CHAT-012 owns it).
- Visual regression testing infrastructure.

## Notes
- The capture runs against the dev server with the same-origin `/api` proxy answered by the mock, which is the shape the real page sees; a Storybook-style harness would not prove the real route wiring.

## Definition of done
- [ ] Screenshots captured and saved as artifacts
- [ ] Checks green on the committed tree
- [ ] Story committed on `feat/hermes-optimizer`

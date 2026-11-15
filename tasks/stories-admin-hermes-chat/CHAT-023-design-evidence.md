# CHAT-023 — Prove the redesign against a mocked backend

## Type
Chore

## Status
Done

## Summary
The redesign is shown, not asserted: screenshots of the empty state, a conversation with markdown, code, and an action card, the sidebar with grouped threads, dark appearance, a streaming turn, and a phone width, all captured against a mocked backend so no key, model, or Hermes host is involved.

## PRD traceability
- Related: FR-022, FR-025
- Out-of-PRD: presentation evidence for the redesign (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-017 … CHAT-022.
- Blocks: none.

## Acceptance criteria

1. Given a mocked chat backend — the server functions and the two `/api/admin/chat/` streams answered from a fixture — when the page is driven in a browser, then every screenshot is produced without a real FastAPI process, Hermes container, or model call.
2. Given the capture, when it is reviewed, then it contains at least these images: empty state, conversation with markdown and a code block, conversation with a proposed action card, sidebar with threads in four date groups, dark appearance, a turn mid-stream with the composer's Stop control, and a 390 px-wide phone layout with its drawer.
3. Given each image, when it is checked, then the centred column, the composer, and the sidebar are visible where the story says they should be, and no screenshot shows a model id, key, or internal host.
4. Given the story's Definition of done, when the branch is inspected, then no mocking code that exists only for the capture is committed: the mock lives outside the repository, and `bun run typecheck`, `bun run lint`, `bun run test`, and `bun run build` pass on the committed tree.
5. Given the image and layout decisions, when the report is written, then it lists what LibreChat shows that this page does not, and why each difference exists.

## Out of scope
- Deployed verification (CHAT-012 owns it).
- Visual regression testing infrastructure.

## Notes
- The capture runs against the dev server with the same-origin `/api` proxy answered by the mock, which is the shape the real page sees; a Storybook-style harness would not prove the real route wiring.
- The mock is a throwaway `http.server` in `/tmp`, not a module under `web/`: the page's real dependency is the proxy, and a committed fixture would be a second thing to keep in step with the generated client.

## Verification note

Captured in Chrome against `vite dev` on `:8080`, with a local mock answering `/admin/optimization`, `/index`, `/admin/chat/threads`, `/admin/chat/threads/<id>`, `/admin/chat/threads/<id>/runs`, the completions stream, and the resume stream. No FastAPI, Hermes, or model call was involved, and the mock is not committed.

Evidence that the states work, beyond the pictures: the send control was enabled after typing, the Stop control was
present while the turn streamed (`stop while streaming: 1`), and after the stream ended the send control was back,
the working indicator was gone, and the reply was on screen (`send back: 1`, `working left: 0`, `reply: 1`). A
page-level check confirmed that the appearance toggle puts `dark` on the document element and that the choice
survives a reload.

One honest note on the first capture: the mock's stream never signalled end-of-body, so the page sat on "Working…"
after the reply text. That was the mock, not the page — with `Connection: close` on the response, the same run
finished and cleared the indicator. It is worth recording because a screenshot of a spinner is exactly the kind of
thing that would otherwise be mistaken for a UI defect. `/search`, `/admin`, and `/chat` were captured after the shell change with no failed requests.

`bun run typecheck`, `bun run lint` (0 errors, 15 pre-existing warnings), `bun run test` (112 cases, 15 files), and `bun run build` pass.

What this page does not do that LibreChat does, and why:

| LibreChat | Here | Why |
| :--- | :--- | :--- |
| Rename, pin, and archive a conversation | Delete only | The backend has no route for the other three; a control that always fails is worse than none |
| Edit a sent message into a new branch | No edit control | Same reason: no edit or branch route exists, and the regenerate path is the only in-place rewrite the API supports |
| Search inside the conversation and the thread list | None | No search route; the thread list is 50 rows from one call |
| Model selector in the composer | A fixed `HERMES chat` label | The model is deployment configuration (`HERMES_CHAT_MODEL`), deliberately never sent to the browser |
| Attachments, voice, slash commands | None | The chat gateway takes text only |
| Token/context meter | None | The backend does not report usage |
| Feedback thumbs | None | No feedback adapter on this surface; the Ask page owns those |
| Export the conversation | None | Not requested, and the audit trail is the operator's record |

Everything else is matched: a collapsible sidebar with **New chat** at the top, date-grouped threads with hover delete and an active highlight, an account footer, a slim header with the thread title and a sidebar toggle, a centred `max-w-3xl` column, assistant prose without a bubble, a subtle user bubble, markdown with copyable code, a hover action bar with copy and regenerate, the composer docked with auto-grow and a Stop swap, a disclaimer line, and light and dark appearance down to a phone.

# CHAT-010 — The chat UI in the Admin section

## Type
Feature

## Status
Draft

## Summary
An admin opens `/admin/chat` and gets a usable chat: streamed markdown replies, a thread history, job progress, stop
and retry, and action cards they confirm themselves. A member sees a denied page, and a deployment with chat off
shows an explicit disabled page that makes no chat request at all.

## PRD traceability
- Related: FR-022, FR-023, FR-025, FR-026, FR-027, FR-028
- Decisions: D-1, D-2 (disabled state and the `chat_enabled` flag), D-3 (misconfigured state), D-4 (delete
  dialog copy)
- Journeys: J1, J2, J3, J4 in the browser
- NFR touchpoints: NFR-6 (accessibility), NFR-7 (no new chat runtime package), NFR-3 (thread load is not slow)

## Dependencies
- Requires: CHAT-009 (service, schemas, SSE reader) and, through it, every route from CHAT-003…CHAT-008.
- Blocks: CHAT-012 (J1–J4 on the deployment).

## Acceptance criteria

Route and access:

1. Given `web/src/routes/admin_.chat.tsx` — the `_` suffix keeps it out of `admin.tsx`, which renders no
   `<Outlet />`, while the URL stays `/admin/chat` — when it is read, then it has the `admin.tsx` shape: `head`, a
   loader that returns `null` for a non-admin, and a component that renders `<AdminChatDenied />` for a member. The
   page lives in `web/src/features/admin-chat/`, per `web/AGENTS.md`. `routeTree.gen.ts` is regenerated, never
   hand-edited.
2. Given `web/src/components/layout/navigation.ts`, when it is read, then the Admin group has `/admin/chat`
   labelled "Hermes chat" with a `Sparkles` or `Bot` icon directly after "Optimization", `/admin/chat` is in
   `ADMIN_PATHS` (so a member's sidebar omits it), and `AppPath` in `web/src/components/common/nav-link.tsx`
   includes the literal.
3. Given `AppSidebar`'s active computation, when the location is `/admin/chat`, then exactly one item is active
   ("Hermes chat"), and when it is `/admin`, then exactly one is active ("Optimization"). The check becomes an
   exact or longest-prefix match; a test renders both.

Disabled state (D-2, FR-027):

4. Given `GET /admin/optimization`, when it is called, then the payload has `chat_enabled: bool` read from
   `settings.HERMES_CHAT_ENABLED` in the existing `_status` handler of
   `src/deepfix_rag/api/routes/optimization.py`. It is not a new route, and the eleven-route count in NFR-4 is
   unchanged. This is the one backend change in this story: a pytest case in `tests/test_api_optimization.py`
   asserts `chat_enabled` is `false` by default and follows the setting, and `web/openapi.json` is regenerated.
5. Given `chat_enabled: false`, when `/admin/chat` renders, then it shows `<AdminChatDisabled />` with the heading
   "Hermes chat is turned off on this server." and one sentence naming `HERMES_CHAT_ENABLED=true`, there is no
   composer, no thread list, no retry, and the page makes zero requests to any `/admin/chat/*` route. A test with
   a fetch spy asserts exactly zero chat calls (the optimization query itself is allowed).
6. Given `ApiModel`'s `json_schema_serialization_defaults_required=True`, when `chat_enabled` is added, then
   `web/src/test/optimization-page.test.tsx`'s `status()` fixture gains `chat_enabled` and the suite compiles; one
   case uses `false`. Every other `OptimizationStatus` literal in tests gains the field too, and a test asserts the
   Optimization screen renders unchanged with `chat_enabled: true`.
7. Given a 503 with `code: "chat_misconfigured"`, when the page receives it, then it renders a diagnostic message
   naming the missing variables, visually and textually distinct from the disabled state. The UI branches on
   `code`, never on the sentence.

Runtime and streaming:

8. Given the page, when it mounts, then it uses `@assistant-ui/react` `^0.15.28` with `AssistantRuntimeProvider`
   and `useExternalStoreRuntime`, the pattern `web/src/features/chat/chat-page.tsx` already uses, plus
   `ThreadPrimitive`, `MessagePrimitive`, `ComposerPrimitive`, `ThreadListPrimitive`, and `ActionBarPrimitive`.
   `onNew` sends a turn, `onReload` sends a retry, `onCancel` aborts and records the cancel; each of the three
   handlers exists. The verification note records that the installed 0.15.28 supports them.
9. Given a streamed reply, when frames arrive, then text appends without reloading the thread and the viewport
   follows until the admin scrolls up.
10. Given a `hermes.tool.progress` event, when it arrives, then it renders as a collapsed step row ("Searching the
    web…", then a result preview).

History and delete:

11. Given the thread list, when it renders, then it is newest first, on the left, with a **New chat** control and a
    delete control per thread. Selecting a thread loads its messages; a reload reopens the thread from
    `?thread=<id>` or the newest thread.
12. Given the delete control, when pressed, then a confirmation dialog names what is removed (the conversation, its
    messages, and any pending action cards) and states that the record of any run started from it — who started it,
    not what was said — is kept (D-4). Confirm calls `deleteThread` and removes the row only after the server
    answers; there is no optimistic removal, and the dialog is the only place a thread is deleted.

Rendering:

13. Given assistant text, when it renders, then it goes through `react-markdown` with `remark-gfm`; a fenced code
    block renders in a bordered `pre > code` with a copy button and no syntax highlighter. User messages render as
    plain text, never as HTML.
14. Given a linked optimization run, when it renders, then the run panel shows status, elapsed, reason, dev delta,
    aggregate holdout delta, wins and losses, the MLflow link, and the step tail, polled every 5 seconds while the
    run is `running` and once on mount.

Stop, retry, actions, header, reconnect:

15. Given a running turn, when the composer is shown, then it is a **Stop** button; pressing it aborts, keeps the
    partial text, and shows "Stopped". Each assistant message has a retry control that resends its user message.
16. Given an action card, when it renders, then it states the effect in one sentence (`Start a run now` /
    `Cancel run <id>`), the assistant's reason, and **Confirm** and **Dismiss**. Confirm shows the resulting run id
    and status; a 409 or 503 renders inline on the card and the card stays confirmable when the failure is
    retryable.
17. Given the page header, when a run is in progress, then it says so, using `optimizationQueryOptions()`; a
    running job disables nothing in the chat.
18. Given a turn that was running when the tab closed, when the thread is reopened, then `resumeTurn` is called and
    its tail is appended, and a test drives `resumeTurn` with a stored partial message and asserts the text appears
    once, not twice. A still-`running` run panel resumes polling.

Accessibility (FR-025):

19. Given the page, when it is inspected, then the composer's textarea has a label, the streamed region is
    `aria-live="polite"`, the stop and confirm controls are real buttons with accessible names, focus returns to the
    composer after a turn, the thread list is a labelled list, and error text uses `role="alert"` as the existing
    pages do. The disabled and misconfigured pages use the same heading level and labelled-region pattern as the
    rest of the Admin section.

Tests:

20. Given `web/src/test/admin-chat-page.test.tsx`, when it runs, then it asserts the composer label, the stop
    button, the denied page for a member, an action card's accessible name, the "no threads yet" empty state, the
    disabled state with zero chat fetches, the misconfigured message, and the delete dialog's accessible name and
    its two buttons.

## Test commands

```bash
cd web
bun run typecheck
bun run lint
bun run test
bun run test src/test/admin-chat-page.test.tsx
bun run test src/test/optimization-page.test.tsx
bun run test src/test/pages.test.tsx
bun run build
```

Browser verification (required for this story):

```bash
# Terminal 1: the API (needs a .env with the chat settings; HERMES_CHAT_ENABLED=true for the chat cases)
uv run python -m deepfix_rag.api.export_openapi
uv run uvicorn deepfix_rag.api.app:create_app --factory --port 8000
# Terminal 2: a stub chat profile on 8643 that speaks chat.completion.chunk, when no container is available
# Terminal 3: the web app
cd web && npm run dev
```

Record in the verification note: an admin sees a streamed reply, a step row, a stop that leaves partial text, a
retry, an action card that starts a run, and a delete confirmation; a member opening `/admin/chat` sees the denied
page and no composer; with `HERMES_CHAT_ENABLED=false` the page shows the disabled state and the network panel
shows no chat request.

## Out of scope
- The SSE transport, service, and schemas (CHAT-009).
- Any change to the Ask screen, its local-storage threads, or its tests.
- An audit view in the UI (the PRD keeps the audit read as an API route).
- Model selection, attachments, images, voice, dictation, or scheduled chat jobs.
- A second chat runtime package or an upgrade of `@assistant-ui/react`.

## Notes
- `admin.tsx` renders no `<Outlet />`; the `_` suffix on `admin_.chat.tsx` is what keeps the page from nesting.
  Confirm the generated `routeTree.gen.ts` carries the sibling, not the child, and commit the regenerated file.
- The disabled state is decided by `chat_enabled` from the optimization query, not by calling a chat route:
  calling one would break FR-027's zero-request rule. Do not add a probe.
- `AppSidebar` uses `active.startsWith(item.to)` today, so `/admin/chat` highlights both entries. The fix is a
  longest-prefix match over the item set (or an exact match for leaf routes); a test at both URLs pins it.
- Reuse `optimizationQueryOptions()` for the header and for `chat_enabled`; do not add a second query.
- Keep user text out of `dangerouslySetInnerHTML`. Markdown renders assistant text only.
- The delete dialog's copy is a product string with a D-4 commitment in it; take the wording from the PRD rather
  than inventing a shorter variant.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; `bun run test` fully green and the existing Ask and Optimization tests unchanged
- [ ] Browser verification recorded in a verification note appended to this file
- [ ] `bun run typecheck`, `bun run lint`, and `bun run build` pass
- [ ] Committed on its own before CHAT-011 starts

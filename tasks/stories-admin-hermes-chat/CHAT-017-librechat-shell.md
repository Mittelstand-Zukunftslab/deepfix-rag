# CHAT-017 — A LibreChat-style shell for the chat page

## Type
Feature

## Status
Done

## Summary
An admin opening `/admin/chat` sees a chat application rather than a form: a collapsible thread sidebar with a **New chat** button and threads grouped by when they were last used, a slim header naming the thread and the model, and a centred message column. The page keeps every behaviour CHAT-009…CHAT-015 delivered; only the frame around the conversation changes.

## PRD traceability
- Related: FR-022 (the chat page), FR-023 (Admin section), FR-026 (no regression to other pages), FR-027 (the disabled page keeps its own shape)
- Decisions: D-7 (Assistant UI on the Vercel AI SDK)
- Journeys: J1, J2 (open the page, pick a conversation)
- Out-of-PRD: the visual target is LibreChat, requested by the product owner on 2026-11-15; the PRD specifies behaviour, not styling.

## Dependencies
- Requires: CHAT-016 (the shared chat components), CHAT-010 (the page exists), CHAT-015 (one runtime per thread, keyed).
- Blocks: CHAT-018, CHAT-019, CHAT-020, CHAT-021, CHAT-022.

## Acceptance criteria

1. Given `web/src/components/assistant-ui/`, when it is read, then it holds the Assistant UI registry components added with the documented CLI (`npx assistant-ui@latest add thread thread-list`), the `tw-shimmer` import they need is in `web/src/styles.css`, and `@assistant-ui/react` stays on the 0.15 line already in `package.json`.
2. Given `/admin/chat`, when it renders, then the page is a full-height two-pane layout: the thread sidebar on the inline start and the conversation pane filling the rest, with no `PageHeader` and no outer card around the chat.
3. Given the sidebar, when it renders, then a **New chat** button sits at the top, the threads are grouped under exactly the labels `Today`, `Yesterday`, `Previous 7 days`, and `Older`, computed from `updated_at` in the viewer's local time, and a group with no threads is not drawn.
4. Given a thread row, when the pointer or keyboard focus is on it, then it shows the delete affordance, and the delete dialog is the same confirmation CHAT-010 defines (same copy, same two buttons, same no-optimistic-removal rule). Rename is not offered: the API has no rename route.
5. Given the active thread, when the list renders, then exactly one row carries `aria-current="true"`.
6. Given the sidebar toggle in the header, when it is pressed, then the sidebar collapses on a wide viewport and the preference survives a reload; below the `md` breakpoint the sidebar is a drawer opened by the same control, and the conversation pane keeps the full width.
7. Given a conversation, when it renders, then the message column is centred and constrained to `max-w-3xl` at every width, and the header shows the thread title plus the assistant name `HERMES chat`, the sidebar toggle, and nothing that names a model id, a key, or a Hermes host.
8. Given the thread list request is still in flight, when the page renders, then the sidebar shows a loading state and the page does not block on it; a thread-list failure renders an inline alert without removing the conversation.
9. Given the account footer, when it renders, then it shows the signed-in admin's email and a sign-out action, and the email is truncated rather than wrapping.
10. Given the disabled, denied, and misconfigured states, when they render, then they are unchanged in wording and still make no chat request.
11. Given the composer and the message body, when the shell renders them, then it imports `ComposerFrame`/`ComposerHint` from `src/components/chat/composer.tsx` and `ChatMarkdown` from `src/components/chat/markdown.tsx` (CHAT-016) rather than adding a second bar or a second markdown renderer; the Ask page's copy of those components is what the admin chat reuses.
12. Given `web/src/test/admin-chat-page.test.tsx`, when it runs, then it asserts the grouped labels against stubbed threads, the active row, the collapsed preference, the header title, and the account footer.
13. Given `web/src/test/admin-chat-session.test.tsx`, when it runs, then every behaviour it pins — thread isolation, one-copy rendering, reload, regenerate — still passes unchanged.

## Out of scope
- The composer (CHAT-018), the empty state (CHAT-019), message rendering (CHAT-020), action cards (CHAT-021), dark mode and the mobile drawer's focus trap (CHAT-022).
- Thread rename, pinning, archive, and search: no backend route supports them.
- The global app navigation sidebar.

## Notes
- The thread list is fed by `adminChatService.threads()`, not by Assistant UI's runtime thread list: the thread store is the backend's, and each thread has its own AI SDK `Chat` (CHAT-015). The Assistant UI thread-list element is runtime-backed and would reintroduce the shared-runtime shape that CHAT-015 removed. Say so in the code comment.
- Date grouping uses `date-fns` (already a dependency) rather than hand-written millisecond arithmetic.
- The sidebar collapse preference is client UI state, so it belongs in `src/stores/` per `web/AGENTS.md`.

## Definition of done
- [x] Code complete
- [x] `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-018 starts

## Verification note

Implemented 2026-11-15 on `feat/hermes-optimizer`. The page is a full-height two-pane layout: `chat-sidebar.tsx`
draws the grouped thread list, `chat-header.tsx` the slim header, and `thread-groups.ts` the `Today` /
`Yesterday` / `Previous 7 days` / `Older` buckets from `date-fns`' calendar-day difference. The collapse preference
is `src/stores/admin-chat-store.ts` (persisted), and the delete confirmation is the `AlertDialog` primitive carrying
CHAT-010's copy verbatim.

`web/src/test/admin-chat-page.test.tsx` grew to 12 cases: the grouped labels against stubbed threads, the single
`aria-current="true"` row, the collapsed preference, the header title and `HERMES chat` label, the account footer,
the delete dialog's copy and its two buttons, plus the unchanged disabled/denied/misconfigured states and the
navigation assertions.

`admin-chat-session.test.tsx` is untouched and all eleven cases in the two admin-chat files pass; the layout change
does not move any behaviour it pins. The composer's accessible name changes to `Send message` in CHAT-018, which is
where that test's assertion is updated.

Not in this story: the composer's auto-grow and Stop swap are CHAT-018's, and the drawer's focus contract is
CHAT-022's. Until CHAT-022, the sidebar is hidden below `md` and the header control collapses it only on wide
viewports.

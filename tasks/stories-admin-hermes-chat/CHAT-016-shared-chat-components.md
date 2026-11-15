# CHAT-016 — One set of chat components for Ask and Hermes chat

## Type
Refactor

## Status
Done

## Summary
The Ask page already renders a chat: a composer bar with a hint, a markdown answer body with copyable code blocks, a message stream, and an empty state with starter prompts. The Hermes chat gets those same pieces instead of a second, parallel implementation. What is genuinely shared moves to `src/components/chat/`; the Ask page keeps behaving exactly as it did.

## PRD traceability
- Related: FR-022 (both chat surfaces), FR-026 (the Ask screen is unchanged except where noted)
- Decisions: D-7 (Assistant UI on the Vercel AI SDK, for the admin chat)
- Out-of-PRD: the reuse requirement came from the product owner on 2026-11-15, with the LibreChat redesign.

## Dependencies
- Requires: nothing new; it is the first story of the redesign.
- Blocks: CHAT-017 (the shell imports these components), CHAT-018 (the admin composer sits in the shared bar).

## Acceptance criteria

1. Given `web/src/components/chat/`, when it is read, then it holds exactly two modules — `markdown.tsx` (`ChatMarkdown`: markdown through `react-markdown` + `remark-gfm`, with a fenced code block that shows its language and carries a `Copy` button) and `composer.tsx` (`ChatComposerBar`, `ChatComposerHint`) — and no module under `src/features/` holds a second markdown renderer or a second composer bar.
2. Given `web/src/features/chat/chat-page.tsx`, when it is read, then its answer body renders through `ChatMarkdown` and its composer bar through `ChatComposerBar`/`ChatComposerHint`, `react-markdown` and `remark-gfm` are no longer imported there, and no other Ask-page markup or behaviour changes.
3. Given the Ask page's own tests (`web/src/test/pages.test.tsx`), when they run, then they pass **unchanged**: the `Ask` textbox, the `Ask` button, the `Answer` label ordering against the `Passages` heading, the vote controls, the error alert, the per-user storage keys, and the `indexReady={false}` state.
4. Given a member's answer containing a fenced code block, when it renders on the Ask page, then the block shows its language, its text is copyable through a `Copy` button, and inline code still renders inline.
5. Given the Ask page's answer rendering, when it is compared with the previous implementation, then the answer body is still inside the same `prose prose-sm dark:prose-invert max-w-none text-body-md leading-relaxed text-foreground` block, so the answer's typography does not change.
6. Given `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build`, when they run, then all pass, and `web/src/test/api-boundary.test.ts` still exempts only the one stream reader.

## Out of scope
- Moving the Ask page onto Assistant UI's AI SDK runtime, its own thread model, or its localStorage session store. The Ask page answers one question at a time against `askService` and keeps that.
- A shared send button. The Ask page's request cannot be cancelled and shows a spinner; the admin chat's must become **Stop** mid-stream (CHAT-018). They are different controls inside one shared bar.
- A shared history sidebar: the Ask page's is per-user localStorage sessions, the admin chat's is the backend thread store.

## Notes
- `ChatMarkdown` takes `prose={false}` in the admin chat, whose assistant messages are spaced by the message column, and keeps the wrapper the Ask page always had. AC 5 exists so a later cleanup cannot quietly restyle every Ask answer.
- A fence is recognised by a `language-*` class or a newline in the content, so the Ask page gains the code-block copy button the admin chat was going to get, from one implementation rather than two.

## Verification note

`web/src/components/chat/markdown.tsx` and `web/src/components/chat/composer.tsx` are the extraction; `chat-page.tsx` lost its `react-markdown`/`remark-gfm` imports and its inline composer classes. `pages.test.tsx` was not edited and passes, which is the evidence for AC 3.

`bun run typecheck`, `bun run lint` (0 errors), `bun run test` (102 cases, 15 files), and `bun run build` all pass on the committed tree.

The admin chat's shell and composer land in CHAT-017 and CHAT-018; the code-block copy button is exercised there through this same component.

## Definition of done
- [x] Code complete
- [x] The Ask tests pass untouched
- [x] `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build` green

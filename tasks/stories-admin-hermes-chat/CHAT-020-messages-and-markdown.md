# CHAT-020 — Messages, markdown, and the action bar

## Type
Feature

## Status
Draft

## Summary
Assistant replies render as prose in the centred column — no bubble — with markdown, code blocks that carry a copy button, and a hover action bar offering copy and regenerate. User messages sit in a subtle bubble with an avatar label. Regenerate still replaces the answer in place.

## PRD traceability
- Related: FR-022, FR-025
- Decisions: D-7
- Out-of-PRD: the visual target (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-017, CHAT-018.
- Blocks: CHAT-021 (action cards render inside this message stream).

## Acceptance criteria

1. Given the registered components, when `web/src/components/assistant-ui/elements/markdown-text.tsx` and `message-actions.tsx` are read, then they are the Assistant UI `markdown-text` and `elements-message-actions` registry items installed with the CLI.
2. Given an assistant message, when it renders, then its text goes through `MarkdownTextPrimitive` (Assistant UI's markdown component) with `remark-gfm`, headings, lists, links, tables, blockquotes, and fenced code render, and the message has no bubble background.
3. Given a fenced code block, when it renders, then it shows its language, has a horizontal scroll rather than wrapping, and carries a copy button with the accessible name `Copy`.
4. Given a user message, when it renders, then it renders as plain text in a rounded, muted bubble, is never interpreted as markdown or HTML, and carries the label `You`.
5. Given an assistant message, when it renders, then it carries the label `HERMES chat` and an avatar mark, and the label is not repeated on every paragraph.
6. Given the pointer leaves an assistant message, when nothing is focused inside it, then its action bar is hidden but still reachable by keyboard focus; on hover or focus it shows copy and regenerate.
7. Given the last assistant message, when it is not the last answer, then no regenerate control is offered on it.
8. Given the regenerate control, when it is pressed, then it uses `ActionBarPrimitive.Reload`, posts `regenerate: true` with the messages up to and excluding the answer it replaces, and the new answer replaces the old one in place — the existing CHAT-014/015 behaviour, unchanged.
9. Given a running reply, when text is still arriving, then a streaming indicator is visible under the message and it disappears when the run settles.
10. Given the message viewport is scrolled up during a stream, when new text arrives, then a scroll-to-bottom control appears with the accessible name `Scroll to bottom` and pressing it returns to the newest message.
11. Given `web/src/test/admin-chat-session.test.tsx`, when it runs, then the regenerate case still asserts the replaced answer and the single `data-role=assistant` node, and a new case asserts a markdown reply renders a `pre > code` block with a copy button.

## Out of scope
- Editing a sent message: the backend has no edit or branch route, so `ActionBarPrimitive.Edit` is not offered.
- Syntax highlighting, LaTeX, mermaid, images, and files.
- Feedback thumbs: the chat has no feedback adapter.

## Notes
- The `Reload` primitive is the whole regenerate mechanism; the `regenerate` flag in the transport is what makes the backend replace rather than append. Do not add a second regenerate path.
- `MessagePrimitive.Parts` still receives the `data-deepfix-tool` renderer, so the step row keeps working.

## Definition of done
- [ ] Code complete
- [ ] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-023 starts

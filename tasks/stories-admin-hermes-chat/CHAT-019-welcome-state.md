# CHAT-019 — A welcome state with starting points

## Type
Feature

## Status
Draft

## Summary
An empty thread shows a centred greeting and three suggestion chips — start an optimization, show the champion configuration, summarise recent runs — instead of a bare "no messages yet" line. Picking one fills the composer so the admin can edit before sending.

## PRD traceability
- Related: FR-022, FR-025
- Decisions: D-7
- Out-of-PRD: the visual target (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-017 (layout), CHAT-018 (the composer a suggestion fills).
- Blocks: CHAT-023 (screenshots of the empty state).

## Acceptance criteria

1. Given the registered components, when `web/src/components/assistant-ui/elements/empty-state.tsx` is read, then it is the Assistant UI `elements-empty-state` registry item, installed with the CLI, and the page imports its `EmptyState`, `EmptyStateGreeting`, `EmptyStateSuggestions`, and `EmptyStateSuggestion` parts instead of writing a new welcome.
2. Given a thread with no messages, when it renders, then the greeting is the only `h2` in the conversation pane and the thread's message list is empty.
3. Given the suggestion row, when it renders, then it offers exactly three chips whose labels are `Start an optimization run`, `Show the champion configuration`, and `Summarize the recent runs`, each a `<button>`.
4. Given a chip, when it is pressed, then its prompt is placed in the composer and **nothing is sent** until the admin presses send; the composer keeps focus.
5. Given a thread that already has messages, when it renders, then the welcome state is absent.
6. Given a reply streaming in an otherwise empty thread, when the first token arrives, then the welcome state disappears and the message column keeps its scroll position at the newest message.
7. Given the page with no thread open at all, when it renders, then the same welcome is shown and pressing a chip creates a thread and sends that prompt.
8. Given `web/src/test/admin-chat-page.test.tsx`, when it runs, then it asserts the three chips, their accessible names, and that pressing one does not call the completions route.

## Out of scope
- Model-generated follow-up suggestions (the runtime has no suggestion adapter wired and the backend sends none).
- Onboarding tours, prompt libraries, or saving prompts.

## Notes
- `ThreadPrimitive.Suggestion` with an explicit `prompt` fills the composer without a runtime suggestion adapter, which is the documented use for static onboarding prompts. `send` is deliberately not passed: the admin edits first, and a run can never start from one click.
- The prompts avoid naming a run id, a model, or a host, because the page must stay free of deployment detail.

## Definition of done
- [ ] Code complete
- [ ] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-023 starts

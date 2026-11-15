# CHAT-021 — Action cards and run panels in the new design

## Type
Feature

## Status
Draft

## Summary
The proposed-run card and the linked-run panel are restyled as Assistant UI surfaces inside the conversation column. Their behaviour is unchanged: only the admin's confirm starts a run, dismiss marks the card rejected without starting anything, and a failure stays on the card.

## PRD traceability
- Related: FR-013, FR-014, FR-015, FR-016, FR-022
- Decisions: D-1, D-3 (never auto-confirm)
- Out-of-PRD: the visual target (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-020 (the stream the card lives in).
- Blocks: CHAT-023 (the screenshot with a card).

## Acceptance criteria

1. Given a proposed action card, when it renders, then it keeps `actionSentence`, the assistant's reason, **Confirm** and **Dismiss**, the same confirmation endpoint, and the same "nothing happens until you do" sentence; only its visual container changes.
2. Given the confirm control, when it is pressed, then the run starts through `confirmAction`, the card shows its confirmed state, the run is linked to the thread, and the run panel appears — the CHAT-007/008 flow, unchanged.
3. Given the dismiss control, when it is pressed, then `rejectAction` is called, the card shows its rejected state, and no run is started: the completions route is not called by the dismiss path.
4. Given a decided card, when it renders, then both controls are disabled and the card states its status in words.
5. Given a failed confirm, when the server answers 409 or 503, then the message renders inside the card with `role="alert"` and the card remains confirmable.
6. Given the card and the run panel, when they render, then they use the same semantic tokens and radius as the composer and the message column, carry an accessible name (`Start a run now` / `Cancel run <id>` / `Run <id>`), and do not introduce a second card design or a new colour scale.
7. Given a run panel, when it renders, then it shows status, started time, reason, dev and holdout deltas, wins/losses, the MLflow link, and the step tail, exactly as before.
8. Given `web/src/test/admin-chat-page.test.tsx`, when it runs, then it asserts the card's accessible name, both controls, the disabled state after a decision, and that dismiss performs no completions call.

## Out of scope
- A new approval or elicitation primitive: the card is the product's own contract with the backend, and Assistant UI's approval element would change what is sent.
- Run detail pages, polling changes, or a second run panel.

## Notes
- The card is rendered from `state.actions`, not from a message part, because it is server state the page owns. It sits at the top of the message column's footer area so it never scrolls away from the run it will start.

## Definition of done
- [ ] Code complete
- [ ] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-023 starts

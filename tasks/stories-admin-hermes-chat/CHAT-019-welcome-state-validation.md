# Story validation: CHAT-019 — A welcome state with starting points

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-welcome-state.md
- Validated: 2026-11-15
- Verdict: Pass

## Findings

### Blockers
None.

### Should-fix
1. AC 4 originally allowed the chip to send. That would let one click reach the model, and the page's rule is that the admin is the only trigger for anything consequential. The chip fills the composer instead, and the AC states that nothing is sent.
2. AC 7 (no thread open) is a different code path from AC 3 (empty thread); kept as its own AC so a reader knows both are required.

### Nice-to-have
- The three prompts are product copy; keeping them in one exported constant lets the test and the page share them without a second literal.

## Suggested edits (concrete)
- None beyond the above.

## PRD alignment
- FRs: FR-022, FR-025.
- Gaps: none. The PRD does not define an empty state; it is styling with a behavioural rule (no accidental send) attached.

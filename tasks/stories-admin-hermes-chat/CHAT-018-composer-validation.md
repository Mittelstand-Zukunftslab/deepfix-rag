# Story validation: CHAT-018 — The Assistant UI composer

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-composer.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. AC 7's draft composer could be read as a second implementation. The Notes now name the single shared shell component, and AC 7 says "the same shell".
2. AC 5's "send control is disabled" is observable only if the control is a real `<button>`; the primitive renders one. Noted in the AC.
3. The disclaimer copy was unspecified. AC 6 now states what it must convey and that it is not interactive, leaving the wording to the implementation.

### Nice-to-have
- AC 8 (focus returns after a turn) is already covered by a helper in the page; the AC keeps it so a refactor cannot drop it silently.

## Suggested edits (concrete)
- Keep the disclaimer as product copy in one constant with the action-card sentence, so the two cannot contradict each other.

## PRD alignment
- FRs: FR-022, FR-025.
- Gaps: none; the composer's behaviour is FR-022's, and the labelling is FR-025's.

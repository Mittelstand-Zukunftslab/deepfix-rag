# Story validation: CHAT-021 — Action cards and run panels in the new design

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-action-cards.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. AC 3 needed a negative assertion: "no run is started" is only testable if it also says the completions route is not called by the dismiss path. Added.
2. AC 6 risked being unverifiable. Reworded to "the same semantic tokens and radius", which a reviewer checks against `styles.css`, plus the accessible names a test can query.
3. A card that failed to confirm must not become undecidable; AC 5 now says it stays confirmable, which is CHAT-007's rule for a retryable failure.

### Nice-to-have
- Assistant UI's approval element was considered. Rejected, because its payload is not this backend's action contract; the story records that in Out of scope.

## Suggested edits (concrete)
- None beyond the above.

## PRD alignment
- FRs: FR-013, FR-014, FR-015, FR-016, FR-022.
- Gaps: none.

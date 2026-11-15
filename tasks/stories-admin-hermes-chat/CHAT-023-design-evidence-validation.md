# Story validation: CHAT-023 — Prove the redesign against a mocked backend

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-design-evidence.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. The story as drafted did not say what happens to the mocking code. AC 4 now requires the committed tree to be clean of capture-only code and to pass the four checks, so evidence cannot be bought with a hack left behind.
2. "Screenshots" was open-ended. AC 2 fixes the six images, so a reviewer knows the set is complete rather than illustrative.
3. AC 3's "no model id, key, or host" is the page's own rule (FR-026, D-7) applied to the evidence. Kept.

### Nice-to-have
- A visual regression suite is out of scope; the story says so rather than leaving it implicit.

## Suggested edits (concrete)
- Capture against the dev server with the `/api` proxy mocked, not a component harness; the AC states it so the images prove the real route shape.

## PRD alignment
- FRs: FR-022, FR-025.
- Gaps: none.

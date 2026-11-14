# Story validation: CHAT-009 — Web data layer for admin chat

- Story file: tasks/stories-admin-hermes-chat/CHAT-009-web-data-layer.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **AC 1 counted the wrong number of operations.** It said "seven JSON operations"; the API exposes nine JSON routes
   and two streaming routes (`POST /admin/chat/completions`, `GET /admin/chat/turns/{turn_id}/stream`), eleven in
   total per NFR-4.
   *Fixed:* AC 1 now says all eleven `/admin/chat` operations, and states which two are streaming.

### Should-fix

2. **The dependency on CHAT-003…CHAT-008 was soft.** The story said "requires" but not *when* to regenerate. It now
   says the client is generated after CHAT-008's commit, which is the first point at which all eleven operations
   exist — otherwise the generated client would be regenerated three times with churn in between.
3. **`code` extraction was described twice.** AC 8 and the note both cover it; the note now points at
   CHAT-003's `api-result.ts` change as the single implementation site if a shared helper is used.

### Nice-to-have
- The `EXEMPT` entry's exact form (`join("services", "admin-chat-sse.ts")`) is stated with the reason it must be a
  relative path — `EXEMPT.has(relative(SRC, file))` — matching what the actual test file does today.
- AC 11's boundary scan lists the five forbidden strings, matching the PRD.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-003 (no model id or key in `web/src`), FR-022 (the client capability), FR-026 (the one `EXEMPT` addition
  the PRD names).
- Decisions: none directly.
- Gaps: none. The `bun run test` gate matches `.github/workflows/ci.yml`, which the story cites.

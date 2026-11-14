# Story validation: CHAT-006 — Rate limit and audit read

- Story file: tasks/stories-admin-hermes-chat/CHAT-006-rate-limit-and-audit-read.md
- Validated: 2026-11-14
- Verdict: **Pass**

## Findings

### Blockers
None. Every AC names a status code, a header, a row count, or a query shape.

### Should-fix
One, closed while drafting:

1. **"One `rate_limited` row per minute" could be read as a second limiter pass.** The PRD calls this out; the story
   now states in AC 5 that the count reads `admin_chat_turns` only, so an audit write cannot inflate it and a
   refusal cannot ratchet itself.

### Nice-to-have
- AC 10 forbids real sleeping and requires an injectable `now`. Without that the suite would take five minutes per
  run.
- The `thread_deleted` query is pinned to one `LEFT JOIN` (AC 7 and the note), so it cannot become an N+1.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-017, FR-018. All present, including the `Retry-After` header and the two-level cap.
- Decisions: D-4 (audit rows survive a delete and are flagged at read time; the audit route stays available when
  chat is off), D-2 (`code` values are CHAT-003's, not re-declared here).
- Gaps: none. Retention is out of scope with a pointer to the PRD's open question.
- Scope note: an audit **UI** is explicitly out of scope. The PRD names only the API route, and CHAT-010 builds the
  chat page. A validator or reader should not expect a screen for this.

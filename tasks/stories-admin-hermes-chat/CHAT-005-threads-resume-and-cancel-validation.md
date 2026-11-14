# Story validation: CHAT-005 — Threads, resume, and cancel

- Story file: tasks/stories-admin-hermes-chat/CHAT-005-threads-resume-and-cancel.md
- Validated: 2026-11-14 (first pass), 2026-11-14 (second pass, after D-6)
- Verdict: **Pass with actions**

## Second pass — after the retention decision (D-6)

### Blockers
None.

### Should-fix

1. **The settled delete rule was pinned only in the storage story.** Fadel confirmed that a thread delete never
   removes an audit row (D-4), and CHAT-002 asserts it at the module level. But the admin's Delete button is this
   route, so a route-level regression — a stray cascade, or a later edit that deletes the thread's audit rows in the
   handler — would pass CHAT-002's test and fail the decision.
   *Fixed:* AC 4 now requires a route-level test that the delete response is 204 and that the `admin_chat_audit` row
   count for that thread is unchanged, so the decision is pinned where the admin touches it.

2. **The retention prune does not change this story.** The route still deletes nothing from the audit table, and the
   prune is elsewhere (CHAT-002, CHAT-006). No AC changed beyond the new assertion in AC 4; a reader should not
   expect thread delete and retention to interact.

### PRD alignment (second pass)
- FRs: FR-008, FR-009, FR-010, FR-011, FR-012 — unchanged.
- Decisions: D-4 (now pinned at the route as well as the module), D-6 (no interaction: this route never prunes).
- Gaps: none.

## First pass

### Blockers

None. Every AC names a route, an `operation_id`, a status code, a stored turn status, or an `EXPLAIN QUERY PLAN`
result.

### Should-fix

1. **The incremental assistant-text write was implied but not owned.** AC 5's replay only works if the completions
   route stores text as it streams; CHAT-003's AC 19 stores it at the end. Left unsaid, resume would replay an empty
   string until the turn finished.
   *Fixed:* AC 7 states the incremental write and requires the test to assert it happens before the terminal frame.

2. **The exempt status of `DELETE` was stated without the route existing yet.** AC 4 now says this route is the one
   that makes the exemption concrete, and points at CHAT-001's `GATE_EXEMPT_PATHS` constant as its single source.

3. **The live-tail registry had no degradation story.** A4 in the PRD assumes one uvicorn worker. AC 5's "not
   running here" branch is now explicitly the multi-worker and post-restart path, and the note forbids adding Redis.

### Nice-to-have
- AC 13 (index usage) is a testable proxy for NFR-3's load requirement, and the two SQL strings are pinned as module
  constants so the test cannot drift from the queries it checks.

## Suggested edits (concrete)
- None outstanding. Every finding in both passes is applied in the story file.

## PRD alignment (first pass)
- FRs: FR-008, FR-009, FR-010 (the delete half), FR-011, FR-012. All present.
- Decisions: D-4 (the delete order and its audit row are inherited from CHAT-002, and the route stays reachable when
  chat is off).
- Journeys: J1 step 1 (thread list), J3 in full, J4 steps 1–3.
- Gaps: none. FR-010's persistence and restart-survival are covered by AC 10 and AC 14.

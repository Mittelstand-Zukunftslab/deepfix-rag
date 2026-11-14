# Story validation: CHAT-008 — Run progress for a thread

- Story file: tasks/stories-admin-hermes-chat/CHAT-008-run-progress-for-a-thread.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **NFR-4's route inventory had no owner.** The PRD says "a test walks `app.routes` and asserts every path starting
   `/admin/chat` is registered with the admin dependency … and that the exempt pair is exactly the two named".
   CHAT-003 can only check the routes that exist at its commit, and CHAT-012 is an operator story that runs no
   pytest. Without a closing story the invariant would be nobody's.
   *Fixed:* new AC 9 here — the last backend story, after which all eleven routes exist — walks `app.routes`,
   asserts the eleven paths, the admin dependency on each, and exactly two gate-exempt paths.

### Should-fix

2. **The step tail could have included the `----- output -----` section.** The optimizer PRD appends up to 64 KiB of
   final output under that marker, which is exactly the content FR-016's redaction rules most need to exclude. AC 5
   and the note now cut at the marker before taking the last 50 lines.
3. **`fail_stale_runs` parity was asserted by name only.** AC 7 now names the arguments the optimization route
   passes, and the test compares behaviourally.

### Nice-to-have
- AC 4's byte-bound test is described as asserting the bound, not just the output, because a reader could otherwise
  pass it with a whole-file load.
- Deterministic ordering (`started_at` desc, `run_id` tiebreak) for tests.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-016. Present in full.
- Decisions: none. D-4's "runs are never deleted by chat" is AC 8.
- Gaps: none. Paging the step log is out of scope with a pointer to open question 2's recommended default.

# Story validation: CHAT-012 — Deployed verification on Dokploy

- Story file: tasks/stories-admin-hermes-chat/CHAT-012-deployed-verification.md
- Validated: 2026-11-14
- Verdict: **Pass**

## Findings

### Blockers
None. The story is an operator checklist; every AC is a command, a visible UI outcome, or a recorded measurement.

### Should-fix
One, closed while drafting:

1. **"Every chat route returns the disabled 503" contradicted CHAT-003.** The PRD's third validation pass records this
   exact drift; the story now says nine routes 503 and the audit and delete routes answer 200 and 204 (AC 3), matching
   CHAT-001, CHAT-003, CHAT-005 and CHAT-006.

### Nice-to-have
- AC 6 names the first cap to lower (`ADMIN_CHAT_MAX_CONCURRENT_TURNS`) and forbids raising any cap, matching the PRD's
  risk 3.
- AC 15 records the 300 ms thread-load measurement rather than asserting it, matching NFR-3.
- AC 16 requires the failures to be recorded too, not only the passes.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: all, verified end to end; FR-029 and FR-024 are measured here rather than inferred.
- Decisions: D-1 and D-5 are measured; D-2, D-3 and D-4 are observed in the UI.
- Journeys: J1–J4 in ACs 7–11.
- Gaps: none. The story explicitly notes that a cloud agent cannot execute it, so it staying `Draft` after the code
  stories land is correct rather than a sign of unfinished work.

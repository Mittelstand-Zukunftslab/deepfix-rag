# Story validation: CHAT-007 — Action cards

- Story file: tasks/stories-admin-hermes-chat/CHAT-007-action-cards.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **AC 12 asked for a call-argument assertion, which the repo's no-mocks rule forbids.** `AGENTS.md` bans
   `unittest.mock` for domain logic, so "a test asserts it was called with the same arguments" was either
   unwritable or would have smuggled a mock into the suite.
   *Fixed:* AC 12 now proves parity behaviourally — both routes hit the same real database with too few labelled
   questions and must return the same skip status and reason — plus a source assertion that neither route calls
   `start_optimization_run` directly. Both are consistent with the repo's testing rules.

2. **"The same domain function the Optimization screen calls" left duplication possible.** Two routes could call
   `start_optimization_run` with independently drifting arguments.
   *Fixed:* AC 5 requires the two optimization route handlers to be refactored into shared helpers
   (`start_run_for(request, trigger="manual")`, `cancel_run_for(request, run_id)`) that the chat confirm route
   reuses. This is a small, contained change to `src/deepfix_rag/api/routes/optimization.py` and is named as this
   story's work rather than left implicit.

### Should-fix

3. **The action kinds could drift from the prompt.** CHAT-004 quotes the block shape in the policy text; AC 12's
   test now also asserts the example block quoted in the prompt parses (the note says so).
4. **Linking the run to the thread was ambiguous.** Now explicitly CHAT-008's, with the card showing only the
   confirm response until then.

### Nice-to-have
- AC 11's no-promotion walk is described as a source/attribute walk so it cannot be satisfied by a comment.
- AC 10 pins open question 3's recommended default (confirmable during a live stream) and gives it a test.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-013, FR-014, FR-015. All present.
- Decisions: none directly; implements the PRD's action-block design and open question 3's default.
- Gaps: none. The `deepfix.turn` frame's `actions` field is CHAT-003's terminal frame, filled here — the two stories
  agree on the field name in both files.

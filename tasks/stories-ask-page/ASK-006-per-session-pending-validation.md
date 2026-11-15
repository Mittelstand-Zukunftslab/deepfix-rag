# Story validation: ASK-006 — Keep pending state with the session that asked

- Story file: `tasks/stories-ask-page/ASK-006-per-session-pending.md`
- Validated: 2026-11-15
- Verdict: **Pass with actions**

## Findings

### Blockers

None. ACs 2–7 are each one rendered state or one stored turn, and the whole story is observable by
switching sessions in one page.

### Should-fix

1. **AC 1 forbade a design without naming the cost.** "a map or an equivalent per-session record" is
   fine, but it did not say what happens to the map when a session is deleted while it is asking. A
   deleted session's entry must be dropped, or the map grows and a late response re-creates a session
   the member deleted. Added as AC 9.
2. **AC 6 allows parallel asks without saying what the backend sees.** Two simultaneous `POST /ask`
   calls are already safe — the route is stateless — but a reader should not have to prove that. The
   note is now explicit that per-session pending does not need a queue.
3. **The `isRunning` requirement in Notes was the actual bug's second half.** It belongs in an AC, not
   in a note, because a passing AC 5 with a wrong `isRunning` still draws a stray assistant
   placeholder. Promoted to AC 10.

### Nice-to-have

- AC 8's "apart from where they drive a per-session control" is the only soft phrase; it stays because
  the alternative is to freeze assertions that legitimately move when a control becomes per-session.

## Suggested edits (concrete)

- AC 9: deleting a session while it is asking drops its pending entry, and a response that arrives
  afterwards does not recreate the session.
- AC 10: `useExternalStoreRuntime`'s `isRunning` is the active session's pending state.
- Notes: state that parallel asks are safe because `POST /ask` is stateless.

## PRD alignment

- FRs: FR-022 (the Ask screen), US-010 (one question, one answer).
- Gaps: none. The PRD describes the Ask screen as member-facing and single-turn; session isolation is
  the existing localStorage design (`chatStorageKeys`) applied to in-flight state as well as stored
  state.

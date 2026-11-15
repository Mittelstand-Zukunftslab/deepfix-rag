# Story validation: ASKCHAT-004 — Stop, resume, regenerate, and fail honestly

- Story file: `tasks/stories-ask-chat/ASKCHAT-004-stop-resume-regenerate.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. The four outcomes this story covers — stop, resume, regenerate, failure — could be read as four stories. They stay together because they are one state machine over one turn's lifecycle, and splitting them would put the same `cancelled`/`failed`/`resumed` transitions in three files that must agree. Each has its own acceptance criteria block, so a reviewer can check them independently.

### Should-fix

Two were found and fixed.

1. **AC 12 as first drafted said a `superseded` vote action.** That value does not exist: `feedback_events.action` has `CHECK(action IN ('up', 'down', 'clear'))`, so a fourth value needs a table rebuild D-5 exists to avoid. Rewritten to use the landed `clear` action, which `feedback.py` already maps to `mlflow.delete_assessment` — the actual intent.
2. **AC 13 had no status code.** "Refused" is not testable. Now 409, which is what a conflicting state should be, and the admin chat's own action routes already use `CONFLICT` for the same class of refusal.

### Nice-to-have

- AC 3 says "if the stream has any remaining ability to write". That is honest about the ASGI reality — once a client disconnects, writing may fail — and the resumed status is what makes it safe. A stricter wording would be untestable.

## Suggested edits (applied)

- AC 12 rewritten to the existing `clear` action, with the reason.
- AC 13 pinned to 409.

## PRD alignment

- FRs: FR-014 (Stop), FR-015 (regenerate), FR-016 (resume), FR-018 (error states and the regenerate control's presence).
- Decisions: D-4 (Stop cancels the stream, not the wait; regenerate replaces in place and clears votes with `clear`), D-9 (a regenerate does not delete the interaction), D-3 (the grounding value survives a stop).
- Gaps: none. FR-018's UI half — "the UI must offer regenerate for a failed turn and must not for `no_relevant_sources`" — is split: AC 14 asserts the server-visible state, ASKCHAT-007 AC 6 asserts the control.

## NFR touchpoints

- **NFR-2** — AC 1, AC 5, AC 16: the disconnect is a normal end, logged once, no traceback; no row stays `running`.
- **NFR-4** — AC 8: another member's turn id is 404 on the resume route, which is the route a naive implementation forgets to scope.
- **NFR-1** — AC 6: the replayed text appears exactly once, which is the correctness property that makes resume usable rather than merely present.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`98eddf0`) and after the deployed defect
  was fixed (`3b5d24f`)
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 13/14 left the target set implicit | AC 13 states 409 for a running newest turn, and that `failed` is replaceable | FR-018 offers regenerate for a failed answer, so "only answered turns" would have contradicted it; and a running turn must not be replaced under itself |
| 2 | The abort was tested through `TestClient` | AC 18a requires a real `uvicorn` on a loopback port with a real closed connection | `TestClient` cannot abort mid-stream, so the `GeneratorExit` path — the one that actually runs — was untested. This is where the shipped bug lived |
| 3 | Nothing said what a regenerate did to the interaction row | AC 12 says it rewrites the existing row | Inserting a second row breaks the one-row-per-turn rule the thread's join depends on |
| 4 | A failed turn's reporting was unspecified | AC 18 adds the cutoff, the absent verdict, the `ERROR` trace, and the empty export | A resumed failure claimed `no_relevant_sources` and `cutoff: 0.0`, and its answerless interaction would have been filed as a dev question |

### Blockers

One, found on the deployment rather than in review: the cancelled path left the
row `running`, because the relay caught `Exception` and a disconnect raises
`GeneratorExit`. Fixed in the story's own implementation once the real-socket test
existed; that test is now AC 18a.

### Should-fix (applied)

1. **The `finally` is the only writer of the terminal state.** Stated in the
   Notes so a later edit cannot reintroduce a second settle path.

### Nice-to-have

1. A stopped turn's partial text is stored per delta, which is one `UPDATE` per
   token. On the deployment that is a few hundred small writes per answer; it is
   acceptable at this scale and is what makes a resume exact. Batching would trade
   that exactness for write volume, and is not worth it until a measurement says so.

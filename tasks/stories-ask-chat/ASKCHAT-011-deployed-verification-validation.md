# Story validation: ASKCHAT-011 — Prove it on the deployed stack

- Story file: `tasks/stories-ask-chat/ASKCHAT-011-deployed-verification.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. The outcome is single — one recorded operator session that settles what a local test cannot — and every AC names an observation to record rather than an intention.

### Should-fix

1. **AC 10 needed to say a "no" is acceptable.** As first drafted it read as though the model must honour `tool_choice: "required"`. It might not, and FR-005's server-side fallback means the turn still retrieved. The AC now says a "no" is a finding, not a failure, which is what turns the step into a measurement rather than a hope.
2. **AC 8's badge check was underspecified.** "the badge transitions" does not say which states or how to reach them, and they depend on real rerank scores that are hard to force. Rewritten to require each of the four rendered states to be observed at least once, with the question that produced it recorded — the four are the thing that is easy to fake locally and hard to see on a deployment.
3. **AC 13 was implicit.** A verification story that ends with "all good" is not checkable. It now requires the result to be a verification note under `docs/`, and says anything that could become a test is named as a follow-up rather than claimed as covered.

### Nice-to-have

- The story does not number the steps. A runbook is written once and followed in order; numbering invites insertion and renumbering. Each step's AC id is its stable name.

## Suggested edits (applied)

- AC 8, AC 10, AC 13 rewritten as above.

## PRD alignment

- FRs: all of them, verified end to end. FR-022 (no regression on the shipped surfaces) in particular has no other owner: every other story's definition of done runs the unchanged suites, and none can prove the deployed Search page, admin chat, and Optimization screen still work.
- Decisions: D-1 (the BFF streams a body — checkable only on the wire), D-7 (`LLM_MODEL` on the real provider), D-8 (the memory envelope), D-10 (the four rendered badge states).
- Gaps: none. This story is the PRD's ASKCHAT-011, which the Story to FR map already describes as "all, verified on the deployment".

## NFR touchpoints

- **NFR-3** — AC 9: `docker stats` proves no new container and no cap change, and that the API's memory returns to its baseline.
- **NFR-4** — AC 3: no frame carries a model id, a key, or an S3 secret. The stream is built part by part rather than forwarded, so this should hold structurally; the runbook confirms it on the wire instead of trusting the design.
- **NFR-5** — AC 3 and AC 7: no credential and no other member's conversation crosses the boundary.
- **Accessibility** — partly, via AC 4's Stop check and AC 6's resume; the fuller keyboard path is ASKCHAT-007 and -008's tests.

## Note for `check-story-size`, not a finding here

Thirteen acceptance criteria, all observations to record rather than code to write. As an operator session it is a single sitting with a laptop and a deployment.

## Post-implementation validation (2026-11-15)

- Validated: after the runbook was written (`ea90987`)
- Verdict: **Pass as a prepared runbook; not executed**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | The story was listed among the build stories, as if it ended in code | Its Type says **operator runbook** and its Status says prepared, not run | A cloud agent has no deployment, no browser against it, and no provider account. Pretending otherwise would produce a document full of claims nobody made |
| 2 | AC 10 asked whether the model honours `tool_choice: "required"` | Kept, and partly answered ahead of the session: on 2026-11-15 it did **not**, which is D-4a | The measurement that fixed the deployed defect also closed half of this AC; the runbook still records it on the real stack |
| 3 | Nothing linked the runbook to the fix | Step 8 now names the provider behaviour as the thing to confirm live | The workaround shipped on a measurement taken outside a browser; a second, operator-run confirmation is cheap |

### Blockers

None. Every step in `docs/ask-chat-verification.md` records a command or action
and a result field reading `NOT RUN`, so nothing in it can be mistaken for a
passing result.

### Should-fix (applied)

1. `docs/README.md` lists the runbook, so it is discoverable from the docs index
   rather than only from the story folder.

## Post-implementation validation, second pass (2026-11-16)

- Validated: after an operator followed the runbook on the deployed stack
- Verdict: **Pass with actions, applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | Step 10's History row said `/admin/interactions` | It says `/history`, and notes that `/admin/interactions` is that page's **API** | The operator typed the API path in the address bar and got a 404. The route exists; there is no browser page there, and the runbook named the wrong one |

### Blockers

None. The page it meant exists and works; only the instruction was wrong.

### Nice-to-have

1. The browser check that found this also found six UI defects the runbook's steps
   do not cover — a stopped answer vanishing, citation glyphs, a mislabelled badge,
   a failed import in the browser, and untitled rows. Those are now their own fixes
   and story passes; a later runbook revision could fold a browser-visual pass into
   step 3's stop check rather than leaving it to chance.

## Post-implementation validation, third pass (2026-11-16)

- Validated: after a browser recheck on the deployed stack found two remaining defects
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 6 said a reload "resumes or shows its stored text", which accepted a cancelled turn | AC 6 requires the answer to continue, the row to settle `completed`, and the badge to describe that turn | The reload was closing the socket, the server was treating that as Stop, and the answer was lost. A runbook row that accepts "stored text" would have passed the broken behaviour |
| 2 | No step looked at the layout | Step 6a checks a narrow width and the sources panel's own box | The panel overlapped the composer at about 630px and scrolled sideways at desktop width. Neither is reachable from a jsdom test, so no automated check could have caught either |

### Blockers

Two on the second pass: a reload lost the reply, and the panel covered the composer
at a narrow width.

### Nice-to-have

1. Both defects were visual or lifecycle facts that only a browser shows. A later
   revision could make the narrow-width check part of every deployed verification,
   not only this story's.

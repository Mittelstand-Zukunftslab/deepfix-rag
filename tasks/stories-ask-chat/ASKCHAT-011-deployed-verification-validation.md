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

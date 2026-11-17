# Story validation: GRAPH-005 — Let the gate and the workspace build accept `graph_hybrid`

- Story file: `tasks/stories-graph-ontology/GRAPH-005-gate-and-workspace-build.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. Each criterion names a returned value, a message, an exception, or a test.

### Should-fix

1. **AC 5 called a graph-store outage a successful rebuild with an error.** That
   reads as if the build "succeeds badly". Reworded: the rebuild fails, and the
   store's error is the recorded `snapshot.error`. This matches NFR-004.
2. **AC 6 asserted equality of two private sets without naming why they can
   legitimately differ.** They cannot, and saying why is what makes the
   assertion durable: one decides whether a candidate may be *scored*, the other
   whether the workspace can *build* a champion. Added to Notes.
3. **The `test_skills.py` interaction was a real trap left unmentioned.** With
   `graph_hybrid` in `_ALLOWED_BACKENDS`, a skill that names it would pass the
   guard while the agent has no graph host. A reader would not see that from the
   story. Added to Notes, with the reason the guard is not tightened: the
   sandbox-instruction assertion (AC 8) is the real guard.
4. **AC 7 said the message names accepted backends but not the test that checks
   it.** `tests/test_api_index.py` asserts the substring "dense, hybrid, and
   bm25". Fixed by naming that test in AC 7 and in the definition of done, so the
   implementer knows the message change reaches an existing assertion.

### Nice-to-have

- The story could drop the word "atomically" from its out-of-scope line. It is
  the right word for what is excluded, and the sentence already says the
  mechanism used instead, so it stays.

## Suggested edits (concrete)

- AC 5: the rebuild fails; `snapshot.status == "failed"` and the store's error is
  `snapshot.error`.
- AC 7: name `tests/test_api_index.py`.
- Notes: add the "two allow-lists, two reasons" paragraph and the
  `test_skills.py` trap.

All three were applied to the story before implementation.

## PRD alignment

- FRs: FR-018 (ACs 1–3, 9), FR-019 (ACs 4–7).
- Gaps: none. FR-020 (FalkorDB stays optional) is out of scope by construction
  and is GRAPH-006's doc work.

## NFR touchpoints

- NFR-004: AC 5 forbids a silent degradation.
- NFR-006: AC 8 keeps the agent's contract unchanged.

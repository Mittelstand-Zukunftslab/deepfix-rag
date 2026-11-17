# Story validation: GRAPH-002 — Strict, parallel extraction over a document sample

- Story file: `tasks/stories-graph-ontology/GRAPH-002-strict-parallel-extraction.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. Each criterion names a constructor argument, a config default, a rejected
value, a payload field, or a manifest consequence.

### Should-fix

1. **AC 5 asserted `document_count` without saying what it means for a graph
   build.** Today that field is the number of documents handed to the build; with
   a sample it must be the sampled number, or the payload misreports the build.
   Fixed by stating it explicitly, which is also what makes AC 6 checkable.
2. **The interaction with `data.limit` was unstated**, and a reader would assume
   the two limits are the same thing. They are not: `data.limit` is part of the
   corpus hash and limits what the *index* is built over, so changing it
   invalidates the index for a different reason. Fixed in Notes.
3. **AC 8's "both are index-time" had no stated consequence.** Fixed to name it:
   the signature differs, and `eval` reports a stale index. Without that, a
   reviewer would not know the change reaches `eval`.
4. **The `test_skills.py` guard was not mentioned**, and it fails on any new
   signature key whose name is missing from the two pathology skills. Fixed in
   Notes and in the definition of done, so the implementer does not discover it
   in CI.

### Nice-to-have

- A test that asserts the extractor receives `strict=True`. It would be a
  narrow test of a constructor call. Left out: GRAPH-004's integration case
  proves the effect (a disallowed pair is absent from the built graph), which is
  the behaviour that matters.

## Suggested edits (concrete)

- AC 5: add the `document_count` sentence.
- Notes: add the `data.limit` composition paragraph.
- AC 8: name the stale-index consequence.
- Notes and DoD: name `test_skills.py`'s signature-key guard.

All four were applied to the story before implementation.

## PRD alignment

- FRs: FR-002 (two of six keys), FR-007, FR-008.
- Gaps: none. FR-008's "the build metadata reports the sampled count" is AC 5
  and AC 6.

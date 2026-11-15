# Story validation: ASK-002 — Drop passages that are not relevant to the question

- Story file: `tasks/stories-ask-page/ASK-002-relevance-floor.md`
- Validated: 2026-11-15
- Verdict: **Pass with actions**

## Findings

### Blockers

None. AC 10 is the end-to-end case the brief requires, and ACs 1–4 are observable in one response
plus one assertion that no completion was recorded.

### Should-fix

1. **AC 3 asked for a documented behaviour rather than a behaviour.** "the documented behaviour states
   which score is compared" can be satisfied by a docstring while the code does something else. It now
   names the retriever's own score and requires a test that a low-scoring candidate is dropped with
   rerank off too.
2. **AC 7 cannot be checked before the work is done.** A default justified "in the story's validation"
   is circular when the story is validated before implementation. It is reworded to require the number,
   the scale, and the evidence in the commit message and the PR body, which exist at the time the
   default ships.
3. **The response shape for `no_relevant_sources` needed the `error` field spelled out.** A reader could
   reasonably set `error` to `GENERATION_FAILED`; that would be wrong, because nothing failed. AC 4
   already says `error` is null; it is repeated in Notes so it is not lost in the AC's density.

### Nice-to-have

- AC 5's "if that's simple" is a hedge in the brief, not a criterion. It is now an unconditional
  requirement, because storing the dropped rows costs one filter placement and no schema change.

## Suggested edits (concrete)

- AC 3 adds: with rerank off, a candidate below the floor is dropped, and a test covers it.
- AC 7 moves its evidence to the commit message and PR body.
- Notes gains the sentence "`error` stays null: nothing failed."

## PRD alignment

- FRs: FR-9 (the interaction keeps every hit it saw), US-010, US-001 (`answer` owns retrieval and
  synthesis).
- Gaps: none. The PRD never promised that every question returns passages; it promised that answers are
  grounded, which a floor strengthens. `retrieval_only` scoring stays consistent through AC 8.

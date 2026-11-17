# Story validation: GRAPH-004 — The `graph_hybrid` backend: entity retrieval merged with dense

- Story file: `tasks/stories-graph-ontology/GRAPH-004-graph-hybrid-backend.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. The story is one outcome — a question answered from the graph *and* the
index, as citable chunks — and the criteria are observable in a result list, a
completion count, and a payload.

### Should-fix

1. **AC 3 mixed "per entity" with "per pass" without pinning the count.** "one
   `VectorContextRetriever` pass runs per entity" cannot be checked without
   counting calls. Reworded to count retrievals, which the test can observe
   through a counting embed model or a counting graph store.
2. **AC 5's "the chunk text from the corpus" needed the failure mode named.** The
   graph store keeps chunks as `(c:Chunk {text, ...})`, so the assertion is that
   no returned node's text matches `^\\S+ -> \\S+ -> \\S+$`. That is the concrete
   difference between this retriever and the `graph` one, and it belongs in the
   criterion rather than in Notes.
3. **AC 8's zero-weight case was `dense_weight: 0, graph_weight: 0`, which is
   also a plausible "turn it all off" config.** Fixed by stating that equal
   weights is the interpretation, matching PRD FR-015, and by naming the
   one-sided cases so a reader cannot pick a different convention.
4. **AC 11 did not say why the LanceDB table must be non-empty.** Without the
   reason, an implementer might "optimise" it away. Added: the dense side of the
   same config reads its own build, so an empty table silently degrades
   `graph_hybrid` to graph-only.
5. **AC 12 was phrased as a failure without a mechanism.** Fixed by naming where
   the check lives: `EvaluationRunner._require_query_expansion_llm` gains
   `graph_hybrid` to the set of LLM-calling backends, so the failure happens once
   before scoring.
6. **AC 13 was a regression guard with no test.** Fixed by naming the existing
   test that guards it: `tests/test_index_creation.py`'s FalkorDB case runs
   `backend: graph` and must keep passing.
7. **The story did not say what happens when the graph store is unreachable at
   query time.** PRD NFR-004 forbids a silent dense-only fallback, and a reader
   would implement one. Added as AC 15.

### Nice-to-have

- The merge function's normalisation could be its own module. It is a dozen
  lines inside the retriever module, which keeps the story to one new module and
  avoids a second place to look. Recorded here, not in the story.

## Suggested edits (concrete)

- AC 3: count `VectorContextRetriever` retrievals, one per entity, capped by
  `graph_query_entity_limit`.
- AC 5: assert no returned text matches `^\S+ -> \S+ -> \S+$`.
- AC 8: state that both-zero means equal weights.
- AC 11: add the "silently degrades to graph-only" reason.
- AC 12: name `_require_query_expansion_llm`.
- AC 13: name the existing `graph` test as the guard.
- New AC 15: an unreachable graph store raises; it never returns dense-only
  results.

All seven were applied to the story before implementation.

## PRD alignment

- FRs: FR-003 (AC 1), FR-013 (ACs 2–4), FR-014 (ACs 5–6), FR-015 (ACs 7–8),
  FR-016 (ACs 9–10), FR-017 (AC 11), FR-021 (AC 12), FR-002 (AC 3's limit).
- Gaps: none. NFR-004 is AC 15.

## NFR touchpoints

- NFR-002: AC 2 bounds the added completions at one per question.
- NFR-004: AC 15.
- NFR-006: AC 13.

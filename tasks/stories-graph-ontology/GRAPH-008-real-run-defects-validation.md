# Story validation: GRAPH-008 — The two defects the first real run exposed

- Story file: added during the real-run verification, recorded in
  `tasks/stories-graph-ontology/README.md` under "Implementation status"
- Validated: 2026-11-17
- Verdict: **Pass**

## Why this story exists

Every case in `tests/test_graph_hybrid.py` passed while `graph_hybrid` was
broken in two ways that made it behave as a graph-only retriever with no entity
anchoring. Both were invisible to a stubbed test by construction:

1. **The dense half scored `0.0` everywhere.** `perplexity/pplx-embed-v1-4b`
   returns 2560-dimensional vectors whose L2 distances on this corpus are 196–310.
   `llama_index.vector_stores.lancedb` converts `_distance` with
   `np.exp(-results["_distance"])`, which underflows to exactly `0.0` by distance
   ~745. So every dense hit had score `0.0`, `_normalised` min-maxed a constant
   list, and the dense contribution to the weighted merge was zero. A
   `MockEmbedding` makes all vectors identical and every distance `0.0`, which is
   a *different* degenerate case that the min-max branch handled by returning
   `1.0` — so the stub hid it. This also affects the champion's own reported
   scores, but not its ranking, which is why nobody had noticed.
2. **Entity detection never ran.** `structured_predict` requires a
   `PromptTemplate`; passing the prompt as a plain `str` raises
   `ValidationError` inside LlamaIndex's instrumentation before the provider is
   called. `detect_entities` catches every exception and falls back to the raw
   question, so the failure was silent: the graph half still returned chunks, just
   never anchored on an entity. The stub overrode `structured_predict` and never
   validated its `prompt` argument, so it could not fail.

## Findings

### Blockers

None; both fixes are small.

### Should-fix

1. **Fusion had to move off raw scores, not just be rescaled.** Min-max on a list
   that is all `0.0` cannot be repaired by clamping; the information is gone. Rank
   is the part of a retriever result that always survives, on both sides, and it
   is what reciprocal-rank fusion already uses in this codebase. Recorded as R-6.
2. **The rank weights are per-side, so `dense_weight: 0.5` still means "half of
   the merged score comes from dense".** A top-ranked dense hit and a top-ranked
   graph hit both score `0.5`; the tie is broken by insertion order, which favours
   the graph side. Acceptable, and stated here rather than left implicit.
3. **The prompt fix must not swallow errors any more.** The `except Exception`
   around entity detection stays, because a provider blip should fall back rather
   than fail a query, but the fix removes the *deterministic* failure it was
   hiding. A test now asserts one completion is made and its names are returned.

## What was verified

- `tests/test_graph_hybrid.py::test_merge_uses_rank_because_a_side_can_score_all_zeros`
  pins the `0.0`-side case without needing a network.
- `tests/test_graph_hybrid.py::test_entity_detection_uses_the_prompt_template`
  asserts one `structured_predict` call and the names it returns.
- Against the real FalkorDB and the live endpoints: detection returned
  `['Asylanträge']` and `['Bundesregierung', 'Asylanträgen']`; the merged list
  carried both sides' chunks with distinct scores (`1.0000`, `0.9714`, `0.9429`,
  …) where it was all `0.0000` before.
- `ruff check`, `ruff format --check`, and the full `tests/test_graph_hybrid.py`
  module pass (6 cases).

## PRD alignment

- FR-013 (entity detection) and FR-015 (weighted merge) were both implemented but
  not *effective*; this story makes them effective. No FR text changes.
- NFR-002 (one completion per question) now actually happens, which is why the
  eval cost rose from ~$0.0003 to ~$0.002 per question.
- R-6 and R-7 in PRD §10 record both defects and their mitigations.

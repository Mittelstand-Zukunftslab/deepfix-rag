# GRAPH-005 — Let the gate and the workspace build accept `graph_hybrid`

## Type

Feature

## Status

Draft

## Summary

A candidate config with `backend: graph_hybrid` is scored and judged by the
promotion gate on the same rules as any other candidate, and a promoted
`graph_hybrid` champion can be built beside the serving index. Plain `graph` and
`mixed` stay rejected exactly as they are today.

## PRD traceability

- Related: FR-018, FR-019
- Goals: Goal 4; decision 6
- Depends on: GRAPH-004 (the backend must retrieve before it can be judged)

## Acceptance criteria

1. Given a candidate `RAGConfig` with `backend: graph_hybrid`, when
   `promotion._load_candidate` reads it, then it is returned as a config, not as
   the rejection string.
2. Given a candidate with `backend: graph`, then the rejection string is
   unchanged: "The candidate uses a retrieval backend that is not allowed."
3. Given a candidate with `backend: mixed`, then it is rejected with the same
   string. Accepting `graph_hybrid` must not admit `mixed`.
4. Given a promoted `graph_hybrid` champion, when the coordinator runs a
   workspace rebuild, then `UnsupportedIndexBackend` is not raised and the build
   proceeds.
5. Given a `graph_hybrid` champion with no reachable graph store, when the
   rebuild runs, then the rebuild fails: `snapshot.status` is `"failed"` and
   `snapshot.error` carries the store's error. The five core services and the
   default deployment are unaffected.
6. `indexing._ALLOWED_BACKENDS` and `promotion._ALLOWED_BACKENDS` are equal sets,
   and a test asserts that they contain `graph_hybrid` and not `graph` or
   `mixed`.
7. `UnsupportedIndexBackend`'s message names every accepted backend including
   `graph_hybrid`, and `tests/test_api_index.py`'s existing assertion on that
   message still passes.
8. The Hermes optimization instructions still say the round accepts
   `dense, hybrid, and bm25`, and `tests/test_hermes_client.py`'s assertion on
   that sentence still passes — a graph backend is not offered to the agent.
9. `tests/test_promotion.py` extends its existing backend-rejection case to
   cover ACs 1–3, and it passes.

## Out of scope

- Enabling FalkorDB in the default deployment, or adding it to the five core
  Compose services.
- Adding `graph_hybrid` to any HPO search space, or to a skill's suggested
  backends.
- Changing the gate's thresholds, its bootstrap, or its holdout rules.
- Building a candidate graph beside the serving *vector* index and switching
  between them atomically. A promoted graph champion rebuilds the serving build
  the same way any index-time change does.

## Notes

Two allow-lists exist for different reasons and must agree: `indexing.py`'s
decides whether the product workspace can build a champion at all (a gate that
accepts what the workspace refuses would leave serving broken after a
promotion), and `promotion.py`'s decides whether a candidate may be scored. They
are already duplicated today with identical contents; this story keeps them
identical and adds one assertion so a future edit to one is caught.

`tests/test_skills.py` imports `promotion._ALLOWED_BACKENDS` and asserts that
every backend a skill names is in it. Adding `graph_hybrid` widens that set, so
a skill that starts naming `graph_hybrid` would pass the guard while the model
has no graph host. The guard is deliberately not tightened here: the Hermes
sandbox test (AC 8) is what keeps the agent off the graph path.

## Definition of done

- [ ] Code complete
- [ ] `tests/test_promotion.py` extended case passing
- [ ] `uv run ruff check` and `uv run ruff format --check` clean
- [ ] `tests/test_skills.py`, `tests/test_hermes_client.py`, and
      `tests/test_api_index.py` still pass

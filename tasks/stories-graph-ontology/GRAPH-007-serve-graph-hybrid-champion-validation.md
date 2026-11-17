# Story validation: GRAPH-007 — Serve a `graph_hybrid` champion without rebuilding it

- Story file: added during implementation, recorded in
  `tasks/stories-graph-ontology/README.md` under "Implementation status"
- Validated: 2026-11-17
- Verdict: **Pass**

## Why this story exists

GRAPH-004 made `graph_hybrid` build and retrieve, and GRAPH-005 made the gate
accept it. Neither story asked whether the *product* could open the result, and
the answer was no, twice over:

1. `RAGWorkflow._index_exists` returned `False` whenever
   `retrieval.graph_store != none`. `QueryService.__init__` calls it and raises
   `FileNotFoundError` when it is false, so a promoted `graph_hybrid` champion
   could never load; the coordinator would rebuild on every start.
2. `RAGWorkflow._load_existing_index` built a `PropertyGraphIndex` with no
   `vector_store`. For `graph_hybrid` the dense half reads that store, so it
   found nothing, and `QueryService.chunk_count()` raised
   `AttributeError: 'NoneType' object has no attribute 'table'`, which is the
   number the Library page and `GET /index` report.

Both were found by opening a built config through `QueryService` rather than by
reading the code, which is why the story carries a runtime assertion instead of
a unit test.

## Findings

### Blockers

None. The two changes are small and the existing integration case already builds
the index the assertion needs.

### Should-fix

1. **The fix had to be scoped to `graph_hybrid`.** Making `_index_exists` ask
   LanceDB for any graph config would change the plain `graph` backend, whose
   builds deliberately leave LanceDB empty and which re-reads the graph on each
   query. Scoped by backend, and the plain `graph` case was re-run by hand to
   confirm it is unchanged.
2. **A `graph` build must not start returning a loadable index.** The existence
   check for `graph` stays `False`. Recorded here because "why is graph treated
   differently" is the question a reviewer will ask.

## What was verified

- `tests/test_graph_hybrid.py::test_graph_hybrid_extracts_dedupes_and_returns_citable_chunks`
  now opens the built config through `QueryService`, asserts
  `index_load_count == 1` (so nothing rebuilt), asserts `chunk_count() > 0`, and
  asserts the served passages carry `doc_graph_` ids.
- The plain `graph` backend was exercised by hand against the same FalkorDB:
  it still builds, still drops the disallowed pair, and still answers.
- `tests/test_query_service.py`, `tests/test_index_creation.py`,
  `tests/test_api_index.py`, and `tests/test_incremental_index.py` pass.

## PRD alignment

- FR-017 (a `graph_hybrid` build writes both halves) is what makes the fix
  possible; this story is the reader of that requirement.
- NFR-004 (no silent dense-only fallback): the load path now fails loudly rather
  than serving a half-index.
- No PRD FR is changed. FR-019 says a promoted champion must be *buildable*; this
  story makes it *servable*, which the PRD's success definition (§1, item 2)
  already requires.

# GRAPH-006 — The shipped graph config, docs, and the FalkorDB CI job

## Type

Feature

## Status

Draft

## Summary

A developer can build and score a Bundestag graph from a shipped config with one
command, the README and `configs/README.md` describe what that config does and
what it needs, and CI runs the graph integration test against a FalkorDB service
container so the path cannot rot again.

## PRD traceability

- Related: FR-020, FR-022, FR-023, FR-024
- Goals: Goal 5; decision 6
- Depends on: GRAPH-003, GRAPH-004, GRAPH-005

## Acceptance criteria

1. `configs/graph_hybrid_falkordb.yml` exists and validates. It sets
   `retrieval.backend: graph_hybrid`, `retrieval.graph_store: falkordb`,
   `retrieval.graph_extraction_schema: bundestag`,
   `retrieval.graph_build_document_limit: 500`,
   `retrieval.graph_num_workers: 4`, `rerank.enabled: true`,
   `rerank.min_relevance_score: 0.3`, and `data.path_or_repo:
   HF:wirthual/dip-bundestag`.
2. `configs/graph.yaml` and `configs/graph_falkordb.yml` set
   `graph_extraction_schema: bundestag` as well, because their corpus is the
   Bundestag one. They keep `backend: graph` and stay as the `graph`-backend
   references.
3. `configs/README.md` lists `graph_hybrid_falkordb.yml` in the backend
   references with: what it builds, that it needs a FalkorDB host, that the gate
   accepts it, that the first build is sampled at 500 documents, and that the
   merge log is the review surface. The `graph.yaml` / `graph_falkordb.yml` rows
   say the gate still rejects `graph`.
4. `README.md`'s graph port row and its graph-backend paragraph say that
   `graph_hybrid` is the promotable graph backend and `graph`/`mixed` are not,
   and that a `graph_hybrid` champion needs `falkordb` from
   `compose.graphdb.yml`.
5. `README.md`'s index-time key list equals, as a set, the key names of
   `index_time_signature(RAGConfig())` as reported by `deepfix schema --json` —
   so `retrieval.graph_build_document_limit`, `retrieval.graph_num_workers`, and
   the three dedup keys appear if and only if the signature includes them.
6. `tasks/prd-deepfix-rag.md`'s graph passages are corrected in exactly three
   places: the note at the top stops saying "Graph RAG is optional and off the
   promoted path" without qualification; §4.4 and FR-11 name `graph_hybrid` as
   the promotable backend next to `graph` as the manual one; and the paradigm
   list gains `graph_hybrid`. The "Graph RAG partially in pipeline" checklist
   line is updated, not deleted, because the vectorless paradigm in the same line
   is still pending.
7. `tests/test_graph_hybrid.py` exists and contains three cases: the
   deduplication case (GRAPH-003 AC 11), the retrieval case (GRAPH-004 AC 14),
   and a config case asserting AC 1's keys. The module declares
   `pytestmark = [pytest.mark.integration]`.
8. The module skips with a named reason when no graph store is reachable, and
   fails instead of skipping when `DEEPFIX_FALKORDB_REQUIRED=1` is set.
9. `.github/workflows/ci.yml` gains one job, `graph`, that runs the new test
   against a `falkordb/falkordb` service container with
   `DEEPFIX_FALKORDB_REQUIRED=1`, `FALKORDB_HOST=localhost`, and the container's
   port mapped. It runs `ruff check` and `ruff format --check` over the tree, so
   a formatting problem in a new module fails this job too, and it installs
   dependencies with `uv sync --frozen --link-mode=copy` like the other jobs.
10. The `graph` job is a required check in the sense that it fails the run when
    it fails — no `continue-on-error`.
11. The four existing jobs (`api`, `cli`, `web`) are unchanged except where the
    new keys force a doc or skill text update, and the quickstart workflow is
    unchanged.

## Out of scope

- Enabling `falkordb` in `docker-compose.yml` or the default five-service start.
- A nightly or scheduled graph build.
- Publishing benchmark numbers from the 500-document build. This story ships the
  capability and the test, not a measurement.
- Adding `graph_hybrid` to `.agents/skills/` routing or to any HPO space.

## Notes

The CI job's service container is the smallest way to get a real FalkorDB:
`falkordb/falkordb:v4.20.3-alpine`, the tag `compose.graphdb.yml` already pins, so
CI and the Compose host run the same build. The job needs no model key: the
integration test uses a deterministic local embedding model and a stub LLM that
returns fixed triplets, so nothing reaches OpenRouter.

The `rapidfuzz` dependency (GRAPH-003) must be in `uv.lock` before this job can
run, which is why GRAPH-003 lands first.

`tasks/prd-deepfix-rag.md` is a large historical document; only its graph-
specific passages change. Its "Graph RAG partially in pipeline" checklist line
is updated rather than removed, because the vectorless paradigm in the same line
is still pending.

## Definition of done

- [ ] Code complete
- [ ] `tests/test_graph_hybrid.py` passing locally against a FalkorDB host
- [ ] CI's new `graph` job green on the branch
- [ ] `uv run ruff check` and `uv run ruff format --check` clean
- [ ] Docs updated: `configs/README.md`, `README.md`, `tasks/prd-deepfix-rag.md`

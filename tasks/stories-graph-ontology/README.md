# Graph ontology and `graph_hybrid` — story index

Stories for `tasks/prd-graph-ontology.md`, produced with the `create-story`
skill and reviewed with the `validate-story` skill from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills` (the repo's names
are `create-story` and `validate-story`; there is no `create-stories` or
`validate-stories`).

Skill adaptations, stated once here so the files do not repeat them:

- The skill's default directory is `docs/stories/`. This repository keeps
  product documents in `tasks/`, so the stories live in
  `tasks/stories-graph-ontology/`, beside the PRD. The precedent is
  `tasks/stories-admin-hermes-chat/README.md`.
- The skill's id convention is project-wide `S-001…`. The PRD already numbers
  its requirements `FR-001…FR-024`, so the stories use a feature-scoped prefix.
  `GRAPH-001…GRAPH-007` appear nowhere else in `tasks/`, which is what the
  convention protects.
- One file per story, kebab-case slug with the id inside the name, and a sibling
  `*-validation.md` from `validate-story`.

## Order

Stories are implemented one at a time on `feat/graph-ontology`, each tested and
committed before the next starts. A commit message names the story:

```
GRAPH-003: dedupe entities after extraction and log every merge
```

| # | Id | Title | Depends on |
| :--- | :--- | :--- | :--- |
| 1 | GRAPH-001 | A Bundestag ontology selected by `graph_extraction_schema` | — |
| 2 | GRAPH-002 | Strict, parallel extraction over a document sample | GRAPH-001 |
| 3 | GRAPH-003 | Entity deduplication as a logged post-extraction step | GRAPH-002 |
| 4 | GRAPH-004 | The `graph_hybrid` backend: entity retrieval merged with dense | GRAPH-002 |
| 5 | GRAPH-005 | Let the gate and the workspace build accept `graph_hybrid` | GRAPH-004 |
| 6 | GRAPH-006 | The shipped graph config, docs, and the FalkorDB CI job | GRAPH-003, GRAPH-004, GRAPH-005 |
| 7 | GRAPH-007 | Serve a `graph_hybrid` champion without rebuilding it (added during implementation; see the deviations) | GRAPH-004, GRAPH-005 |

Seven stories, one dependency chain, no cycle. GRAPH-003 and GRAPH-004 both build
on GRAPH-002 and are independent of each other; GRAPH-006 is last because it
ships the config that turns all of it on and the CI job that keeps it working.

## Sizing

The PRD's own split is by mechanism, and each story is one mechanism:

- GRAPH-001 is data: a registry entry plus the validation triples. No code path
  changes behaviour except that strict mode starts rejecting pairs the old
  vocabulary allowed.
- GRAPH-002 is the extraction call: two config keys and the extractor arguments.
- GRAPH-003 is a new module and its build step.
- GRAPH-004 is a new retriever module and two `_build_retriever` branches.
- GRAPH-005 is two `frozenset`s and their messages.
- GRAPH-006 is a config file, three docs, one test module, and one CI job.

GRAPH-003 and GRAPH-004 are the two that could overrun a session. The honest
split if that happens: GRAPH-003's merge predicate and its log first, the
Cypher re-pointing second; GRAPH-004's entity detection and per-entity
retrieval first, the dense merge and dedupe second. Both halves are separately
testable and neither is useful alone, which is why they are one story each.

## Fixed decisions (do not re-open in a story)

These are §12 of the PRD. A story assumes them.

- Bundestag DIP ontology, generic `entity_relationship` as the fallback, chosen
  by `retrieval.graph_extraction_schema`.
- `strict=True` with a triple-list `kg_validation_schema`.
- FalkorDB, deduplication in Python, no APOC.
- `graph_hybrid` = entity retrieval + dense, reranked, citable chunks.
- `LLM_MODEL` extracts; `num_workers` is configurable; the shipped sample is 500
  documents.
- The gate may promote `graph_hybrid`; `graph` and `mixed` stay rejected;
  FalkorDB stays optional and off in the default deployment.
- Deduplication: cosine ≥ 0.9 **and** a `rapidfuzz` name match, JSONL log,
  identifiers never merged.

## FR coverage

Every FR is claimed by exactly one story except FR-022…FR-024, which GRAPH-006
owns because they are the test and CI surface.

| Story | FRs |
| :--- | :--- |
| GRAPH-001 | FR-001, FR-004, FR-005, FR-006 |
| GRAPH-002 | FR-007, FR-008, FR-002 (the two extraction keys) |
| GRAPH-003 | FR-009, FR-010, FR-011, FR-012, FR-002 (the three dedup keys) |
| GRAPH-004 | FR-003, FR-013, FR-014, FR-015, FR-016, FR-017, FR-021, FR-002 (`graph_query_entity_limit`) |
| GRAPH-005 | FR-018, FR-019 |
| GRAPH-006 | FR-020, FR-022, FR-023, FR-024 |

FR-002 is the shared config surface and is split by which story introduces each
key; no story duplicates another's key.

## Tests each story adds or changes

Repository rule: meaningful integration tests only. There is no per-function
test.

| Story | Test |
| :--- | :--- |
| GRAPH-001 | Extends `tests/test_config_mutator.py`'s existing `test_graph_extraction_schemas_in_schema_module`: `bundestag` is registered, its relation set is closed, and every allowed triple names a declared type. |
| GRAPH-002 | No new module. The extractor wiring is covered by GRAPH-004's end-to-end case and by the existing config guard. |
| GRAPH-003 | One case in `tests/test_graph_hybrid.py`: a corpus with `BT-Drucksache 20/123` and `BT-Drucksache 20/124` plus one true duplicate merges exactly the true duplicate and logs it. |
| GRAPH-004 | One case in `tests/test_graph_hybrid.py`: a built graph plus a `graph_hybrid` query returns chunks with `doc_id`, and a chunk reached from both sides appears once. |
| GRAPH-005 | Extends `tests/test_promotion.py`'s existing backend case: `graph_hybrid` is allowed by the loader and `graph`/`mixed` are not. |
| GRAPH-006 | `tests/test_graph_hybrid.py` gains the requirement switch (`DEEPFIX_FALKORDB_REQUIRED`) and the CI job runs it. |

## Implementation status (2026-11-17)

One commit per story on `feat/graph-ontology`, each linted, tested and committed
before the next began.

| Id | Status | Notes |
| :--- | :--- | :--- |
| GRAPH-001 | Done | The ontology registry, the all-pairs fallback, and the `Literal` boundary the strict extractor needs. |
| GRAPH-002 | Done | `graph_num_workers`, `graph_build_document_limit`, and the sampled count in the build payload. |
| GRAPH-003 | Done | `graph_dedup.py`: the merge predicate, the identifier guard, the Cypher re-pointing, the JSONL log, and `rapidfuzz`. |
| GRAPH-004 | Done | `graph_hybrid.py`: `EntityGraphRetriever` + `GraphHybridRetriever`, the dense twin in a `graph_hybrid` build, and the config validator. |
| GRAPH-005 | Done | Both allow-lists widened to `graph_hybrid`; `graph` and `mixed` still rejected. |
| GRAPH-006 | Done | `configs/graph_hybrid_falkordb.yml`, the README / `configs/README.md` / PRD updates, and the CI `graph` job. |
| GRAPH-007 | Done | Serving: `_index_exists` and `_load_existing_index` assumed a graph config never has a loadable index. Found while proving a promoted champion answers through `QueryService`; see the deviation below. |

Two deviations from the stories as written, both recorded here rather than
silently applied:

1. **GRAPH-003 AC 11 and GRAPH-004 AC 14 became two cases in one module rather
   than one case each**, and the module also holds a third case for the edge
   contract (GRAPH-006 AC 7). The intent is unchanged: one integration module,
   three meaningful cases, no per-function tests.
2. **A seventh story was added: GRAPH-007, serving a graph_hybrid champion.**
   GRAPH-004 and GRAPH-005 made the backend build and pass the gate, but nothing
   checked that `QueryService` could open the result. It could not:
   `RAGWorkflow._index_exists` returned `False` whenever a graph store was
   configured, so a promoted champion rebuilt on every load, and
   `_load_existing_index` built the graph index without the vector store, so the
   dense half of the backend had nothing to read and `chunk_count()` raised. The
   fix is two small changes plus an assertion in the existing integration case.
   It is a story rather than a silent addition because it is a distinct outcome —
   "a graph_hybrid champion serves" — with its own failure mode.
3. **GRAPH-006 AC 9's CI job runs `tests/test_graph_hybrid.py` by path** rather
   than by a `graph` marker. Adding a marker would mean registering it in
   `pyproject.toml` and in `conftest.py`'s deselect logic for one file; the path
   is what `.github/workflows/quickstart.yml` already does for its own script.
   The job keeps `DEEPFIX_FALKORDB_REQUIRED=1`, so a broken store fails rather
   than skips.

## Test commands

Python, from the repository root:

```bash
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run --env-file .env pytest -v tests/test_graph_hybrid.py        # needs a FalkorDB host
uv run --env-file .env pytest -v tests/test_promotion.py tests/test_config_mutator.py
uv run ruff check
uv run ruff format --check
```

A local FalkorDB host, which is what `compose.graphdb.yml` provides:

```bash
docker compose -f compose.graphdb.yml up -d falkordb
FALKORDB_HOST=127.0.0.1 FALKORDB_PORT=6379 \
  uv run --env-file .env pytest -v tests/test_graph_hybrid.py
```

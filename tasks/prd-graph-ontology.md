# PRD: A Bundestag ontology and a `graph_hybrid` retrieval path

> **Status: accepted, not implemented.** Fadel settled the decisions in this
> document on 2026-11-17. The stories are in `tasks/stories-graph-ontology/`.
> For the shipped behaviour after implementation read `README.md` → "Graph
> retrieval" and `configs/README.md`.

## 1. Overview

### The problem

DeepFix ships a graph retrieval path that nobody can use. `retrieval.backend: graph`
builds a `PropertyGraphIndex` on Memgraph or FalkorDB, but

- extraction is untyped. `GRAPH_EXTRACTION_SCHEMAS` names a vocabulary and then
  hands it to `SchemaLLMPathExtractor` with no `kg_validation_schema`, so a
  "Person" can be `PART_OF` a "Location" and the graph has no invariant to check
  it against;
- the schema knows nothing about the corpus. Bundestag DIP documents are
  Drucksachen, Plenarprotokolle, Vorgänge, Gesetze, committees, parliamentary
  groups, ministries, members, and policy topics (`HF:wirthual/dip-bundestag`).
  Extracting `ORGANIZATION` and `CONCEPT` from them produces a graph that cannot
  answer "which committee was this Vorgang assigned to";
- entities are never deduplicated. `BT-Drucksache 20/123` and `20/123` and
  `Drucksache 20/123` are three nodes, so a traversal reaches a third of the
  neighbourhood;
- retrieval is all-or-nothing. `graph` and `mixed` rank worse than plain `hybrid`
  because the retriever has no idea what the question is about: it embeds the
  whole question and looks for a similar entity. A question naming three
  entities retrieves the neighbourhood of one blurred average of them;
- the promotion gate rejects `graph` and `mixed`, so nothing built on this path
  can ever become the champion, and nobody maintains it.

The result is a path that costs a graph database, an extraction LLM budget, and a
config file, and cannot be promoted. This PRD defines an ontology that fits the
corpus, a build that survives review, and a retrieval backend that competes with
the champion on the same paired gate every other candidate faces.

### Target users

| Actor | What they need |
| :--- | :--- |
| DeepFix operator (Fadel) | A graph path that can win the promotion gate, and a reviewable record of what the build merged. |
| Optimization agent (Hermes) | Nothing new. It keeps running `hybrid` sweeps; the graph path is not a round action. |
| Member using Ask / Search | Answers and citations that do not change shape: passages are still chunks with `doc_id`. |

### Success definition (measurable)

1. `deepfix index` with `configs/graph_hybrid_falkordb.yml` builds a typed graph
   from 500 Bundestag documents, and every relation in that graph is one of the
   nine ontology relations between the type pair the PRD allows (§6.2). A
   relation outside the allow-list is a build defect, not a surprise.
2. `deepfix eval --retrieval-only` with `backend: graph_hybrid` returns ranked
   chunks with `doc_id` metadata, so `Ask` citations, votes, and the grounding
   status keep working with no change to the Ask code path.
3. The merge log `<index_dir>/graph_merges.jsonl` lists every merged entity, and
   a review of the 500-document build finds no pair that differs only in a
   number (§6.4).
4. A `graph_hybrid` candidate is judged by the same paired gate as any other
   candidate (`OPTIMIZATION_MIN_PRIMARY_DELTA = 0.025`, holdout wins > losses)
   and is promoted only when it wins. `graph` and `mixed` stay rejected.
5. CI runs one integration test against a FalkorDB service container that builds
   a small graph and asserts `graph_hybrid` returns linked chunks.

## 2. Goals and non-goals

### Goals

- Define a Bundestag DIP ontology (nine entity types, nine relations, an
  explicit source→target allow-list) and make it selectable through the existing
  `retrieval.graph_extraction_schema` key, with `bundestag` as the shipped graph
  config's value.
- Keep the generic `entity_relationship` schema as the fallback for
  user-uploaded documents, and make that fallback explicit instead of implicit.
- Extract in strict mode against the allow-list, with configurable parallel
  workers, on a configurable document sample (500 by default for the shipped
  graph config).
- Deduplicate entities after extraction in Python — FalkorDB has no APOC —
  behind an embedding-similarity **and** name-similarity rule, with a JSONL log.
- Add `retrieval.backend: graph_hybrid`: an entity-aware graph retriever merged
  with the existing dense LanceDB retriever, reranked by the existing reranker,
  returning citable chunks.
- Let the promotion gate accept `graph_hybrid` under the existing rules.
- Keep FalkorDB optional: off in the default deployment, present in
  `compose.graphdb.yml`.

### Non-goals

- **Not** a new embedding or generation model, and no new model setting.
  Extraction and entity detection use `LLM_MODEL`; deduplication uses the
  configured `EMBEDDING_MODEL`.
- **Not** an Ask, Search, or vote change. The contract "results are chunks with
  `doc_id`" is preserved rather than extended.
- **Not** enabling graph retrieval in the default deployment, and not adding
  FalkorDB to the five core Compose services.
- **Not** letting the Hermes optimization agent build graphs. Its sandbox has no
  FalkorDB host, its instructions keep naming `dense, hybrid, and bm25`, and
  `graph_hybrid` is not part of any HPO search space.
- **Not** re-opening plain `graph` or `mixed`. They stay rejected by the gate.
- **Not** a graph-query language surface (Cypher templates, text-to-Cypher) for
  members.
- **Not** entity resolution against an external authority file (DIP person ids,
  Wahlperiode registers). Deduplication is string and embedding similarity only.

## 3. Personas / actors

- **Operator (Fadel):** builds the graph on demand, reads the merge log, decides
  whether `graph_hybrid` is worth enabling, and approves merges of the branch.
- **Optimization round (Hermes):** unchanged. Never sees the graph backend.
- **Member:** asks a question in Ask; receives passages and citations from
  whichever backend the champion uses. Cannot tell which backend answered, and
  must not have to.
- **Reviewer of a build:** a developer who reads `graph_merges.jsonl` and the
  index manifest to decide whether the graph is trustworthy.

## 4. Scope

### In scope

- `src/deepfix_rag/config/schema.py`: the `bundestag` schema entry, relation
  allow-lists per schema, `graph_hybrid` in `RetrievalBackend`, and the new
  retrieval keys (§6.1).
- Extraction wiring in `pipeline.py`: strict mode, validation schema, worker
  count, document sample.
- A new deduplication module and its build step.
- A new retrieval module with the entity retriever, the merge, and the citable
  chunk mapping.
- `promotion.py` and `indexing.py` backend allow-lists.
- `configs/graph_hybrid_falkordb.yml`, `configs/README.md`, `README.md`, and the
  graph sections of `tasks/prd-deepfix-rag.md`.
- One integration test and one CI job with a FalkorDB service container.

### Out of scope

- Ontologies for other corpora (Legal RAG Bench keeps `domain_legal` unchanged).
- Incremental graph updates when a document is added or deleted in the product
  workspace. A graph build is a full rebuild (§9 R-4).
- Graph visualisation, a graph explorer, or an admin page for merges.
- Reranking the graph candidates separately from the dense ones.

### Won't (this release)

- No promotion to production and no deployment change. Fadel reviews merges
  separately, and FalkorDB stays off on the server.
- No `deepfix graph-dedup` command. Deduplication runs as part of the build; the
  log is the review surface.
- No streaming or per-document deduplication during extraction.

## 5. User journeys

### J-1 The operator builds a Bundestag graph (FR-001, FR-002, FR-003, FR-004, FR-005)

1. `docker compose -f compose.graphdb.yml up -d falkordb`
2. `deepfix index --config configs/graph_hybrid_falkordb.yml --index-dir indexes/bt-graph --json`
3. The build samples 500 Bundestag documents (FR-003), extracts typed triplets in
   strict mode with 4 workers (FR-002), writes the graph to FalkorDB, dedupes
   entities and writes `<index-dir>/graph_merges.jsonl` (FR-004), and writes the
   LanceDB chunk index the dense half of retrieval needs (FR-006).
4. The `--json` payload reports the sampled document count, the chunk count, and
   the number of merged entities.
5. The operator reads the log, confirms no identifier pair was merged, and either
   trusts the build or rolls `graph_build_document_limit` back.

### J-2 A question reaches the graph (FR-006, FR-007, FR-008)

1. A member asks "Was hat der Auswärtige Ausschuss zum Vorgang der
   Bundesregierung zur UN-Resolution beschlossen?"
2. The entity retriever asks `LLM_MODEL` which entities the question names, up to
   `graph_query_entity_limit`. It receives e.g. `Ausschuss für auswärtige
   Angelegenheiten`, `UN-Resolution`.
3. For each entity it runs a `VectorContextRetriever` one to
   `graph_traversal_hops` hops deep, collects the source chunk ids it reached,
   loads those chunks from the graph store, and scores each chunk by its best
   entity similarity (FR-007).
4. The dense retriever runs on the same question against LanceDB.
5. The two lists merge, weighted by `dense_weight` and `graph_weight`, deduped to
   one node per chunk, and cut to `top_k` (FR-008).
6. The existing reranker reorders them and the relevance floor drops anything
   below `rerank.min_relevance_score`.
7. Ask renders the passages with their `doc_id` citations exactly as it does for
   `hybrid` (FR-009).

### J-3 A candidate is judged (FR-010)

1. An operator (or a run) writes `candidate.yaml` with `backend: graph_hybrid`.
2. `apply_candidate` builds the candidate index beside the serving one, scores
   dev and holdout retrieval, and applies the existing rules: dev delta ≥ 0.025,
   bootstrap interval above zero, no higher-is-better metric past the tolerance,
   no errored cases, holdout wins > losses.
3. The candidate is promoted only if it wins. A `graph` or `mixed` candidate is
   still rejected before scoring, with the existing reason string.

## 6. Functional requirements

Priority is `Must` for the release unless stated.

### 6.1 Configuration surface

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-001 | `retrieval.graph_extraction_schema` accepts `bundestag` and selects the ontology in §6.2. The registry stays in `config/schema.py` (`GRAPH_EXTRACTION_SCHEMAS`) and gains a validation-schema field per entry. `entity_relationship` becomes the explicit fallback: any unknown or `default` value resolves to it, and an unknown value is not a validation error. The shipped Bundestag graph configs set `graph_extraction_schema: bundestag`. | Must | The key already exists and is already index-time; adding a value costs no new surface. A fallback keeps user-uploaded corpora working. |
| FR-002 | New retrieval keys, all validated by Pydantic with bounds: `graph_num_workers` (int, 1–16, default 4), `graph_build_document_limit` (int ≥ 1 or null, default null), `graph_dedup_similarity` (float 0–1, default 0.9), `graph_dedup_name_ratio` (float 0–1, default 0.9), `graph_dedup_max_edit_distance` (int 0–10, default 2), `graph_query_entity_limit` (int 1–20, default 5). | Must | Every lever in §6.2–§6.6 must be reachable from a config file or an HPO trial, not from code. |
| FR-003 | `RetrievalBackend` gains `graph_hybrid`. A config with `backend: graph_hybrid` and `graph_store: none` fails validation with a message naming both keys. `graph` and `mixed` stay valid enum values. | Must | The backend needs a store; failing in the config beats failing at query time. |

### 6.2 The ontology

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-004 | Entity types are exactly `PERSON`, `FRAKTION`, `MINISTERIUM`, `AUSSCHUSS`, `DRUCKSACHE`, `PLENARPROTOKOLL`, `VORGANG`, `GESETZ`, `THEMA`. Relations are exactly `MEMBER_OF`, `SUBMITTED_BY`, `ANSWERED_BY`, `ASSIGNED_TO`, `PART_OF`, `AMENDS`, `REFERS_TO`, `SPOKE_IN`, `ABOUT`. | Must | A closed vocabulary is what makes strict mode meaningful. |
| FR-005 | `kg_validation_schema` is the triple-list form (the form current `llama-index-core` supports natively) and allows exactly these pairs: `PERSON MEMBER_OF FRAKTION`, `PERSON MEMBER_OF AUSSCHUSS`, `DRUCKSACHE SUBMITTED_BY FRAKTION`, `DRUCKSACHE SUBMITTED_BY MINISTERIUM`, `DRUCKSACHE SUBMITTED_BY PERSON`, `DRUCKSACHE ANSWERED_BY MINISTERIUM`, `VORGANG ASSIGNED_TO AUSSCHUSS`, `DRUCKSACHE PART_OF VORGANG`, `DRUCKSACHE AMENDS GESETZ`, `GESETZ AMENDS GESETZ`, `DRUCKSACHE REFERS_TO DRUCKSACHE`, `DRUCKSACHE REFERS_TO GESETZ`, `DRUCKSACHE REFERS_TO PLENARPROTOKOLL`, `DRUCKSACHE REFERS_TO VORGANG`, `GESETZ REFERS_TO GESETZ`, `PLENARPROTOKOLL REFERS_TO DRUCKSACHE`, `VORGANG REFERS_TO DRUCKSACHE`, `VORGANG REFERS_TO GESETZ`, `PERSON SPOKE_IN PLENARPROTOKOLL`, `DRUCKSACHE ABOUT THEMA`, `GESETZ ABOUT THEMA`, `PLENARPROTOKOLL ABOUT THEMA`, `VORGANG ABOUT THEMA`. | Must | "Person MEMBER_OF Ausschuss" and "Drucksache ANSWERED_BY Ministerium" are different claims; a per-relation allow-list is what the blog post's schema-guided extraction is for. |
| FR-006 | `strict=True` for every schema entry, including the generic fallback. The generic `entity_relationship` and `domain_legal` entries get a generated all-pairs allow-list over their own types, so they stay usable but closed. | Must | Strict mode is the defect that this PRD exists to fix. |

The pairs, and why each exists:

| Relation | Source → target | Meaning in the corpus |
| :--- | :--- | :--- |
| `MEMBER_OF` | Person → Fraktion; Person → Ausschuss | An MP belongs to a parliamentary group and sits on committees; a minister belongs to a group too. |
| `SUBMITTED_BY` | Drucksache → Fraktion, Ministerium, Person | Who introduced the printed paper (Antrag, Gesetzentwurf, Anfrage). |
| `ANSWERED_BY` | Drucksache → Ministerium | A Kleine Anfrage is answered by a ministry. |
| `ASSIGNED_TO` | Vorgang → Ausschuss | The committee a legislative procedure was referred to (`Überweisung`). |
| `PART_OF` | Drucksache → Vorgang | The paper is one step of the procedure. |
| `AMENDS` | Drucksache → Gesetz; Gesetz → Gesetz | A draft changes a law; one law changes another. |
| `REFERS_TO` | document → document, document → law, protocol → paper | Citations between papers, laws, protocols, and procedures. |
| `SPOKE_IN` | Person → Plenarprotokoll | Who spoke in which sitting. |
| `ABOUT` | Drucksache, Gesetz, Plenarprotokoll, Vorgang → Thema | The policy topic, which is what a member's question is usually phrased in. |

### 6.3 Extraction

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-007 | Extraction uses the existing `LLM_MODEL` through the pipeline's LLM (`_get_llm()`), with `SchemaLLMPathExtractor(num_workers=retrieval.graph_num_workers, max_triplets_per_chunk=retrieval.graph_max_paths_per_chunk, strict=True, kg_validation_schema=<triples>)`. No new model setting is introduced. | Must | Decision 5. One model endpoint per deployment. |
| FR-008 | When `retrieval.graph_build_document_limit` is set, the build extracts from at most that many documents (the first N the loader returns), and the build metadata reports the sampled count. The shipped graph config sets 500. | Must | A full DIP build is tens of thousands of documents and hours of LLM calls. A reviewable first build is 500. |

### 6.4 Deduplication

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-009 | Deduplication is a separate step that runs after the graph is written, in Python, with no APOC and no server-side procedure. It groups entities by label and merges two entities only when **both** hold: cosine similarity of the name embeddings ≥ `graph_dedup_similarity`, **and** the names are close — one contains the other at `rapidfuzz` partial ratio ≥ `graph_dedup_name_ratio`, or their Levenshtein distance ≤ `graph_dedup_max_edit_distance`. | Must | Decision 7. Two independent signals keep precision up; one signal is either too loose (embeddings) or too tight (edit distance). |
| FR-010 | Two names whose digit/identifier sequences differ are never merged, even when both signals pass. A cluster is merged transitively (union of pairs), and the canonical name is the longest name in the cluster, ties broken alphabetically. | Must | `BT-Drucksache 20/123` vs `20/124` is the documented false positive. Longest-name canonicalisation keeps the informative form. |
| FR-011 | Every merge is appended to `<index_dir>/graph_merges.jsonl` with one JSON object per merge: `{"label", "canonical", "merged", "similarity", "name_ratio", "edit_distance", "reason"}`. The run reports the number of merged entities in the build payload (`graph_merges`). | Must | Decision 7. Reviewability is the point of the log. |
| FR-012 | Merging re-points every relationship of a duplicate onto the canonical node, keeps the duplicate's name as an alias property on the canonical node, re-links `MENTIONS` from chunks to the canonical node, and deletes the duplicate. It runs through the store's `structured_query` (Cypher), not through a store-specific Python API. | Must | Without APOC this is the only path, and it must work on both Memgraph and FalkorDB. |

### 6.5 `graph_hybrid` retrieval

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-013 | `graph_hybrid` runs a `CustomPGRetriever` subclass that (a) asks `LLM_MODEL` for the entity names in the question, (b) runs one `VectorContextRetriever` per detected entity with `path_depth = retrieval.graph_traversal_hops`, and (c) when no entity is detected, runs it once on the raw question. Detected entities are limited to `graph_query_entity_limit`. | Must | Decision 4 and the blog post's custom retriever. |
| FR-014 | The graph side returns **source chunks**, not triplet text: the retriever maps each reached triplet to its source chunk node (`triplet_source_id`), loads the chunk from the graph store, and scores it with the best entity similarity that reached it. Every returned node carries the metadata of the chunk it came from, including `doc_id`. A triplet whose source chunk cannot be loaded is skipped; it is a dropped candidate, not a retrieval error. | Must | Results must stay citable; a triplet string has no `doc_id`. |
| FR-015 | The graph list merges with the dense LanceDB list, weighted by `retrieval.dense_weight` and `retrieval.graph_weight` (normalised; identical weights when both are zero), deduplicated to one node per `(doc_id, text)` pair with the higher score kept, and cut to `retrieval.top_k`. | Must | The same chunk reached by both sides must appear once, or Ask shows the passage twice and the vote splits. |
| FR-016 | The merged list is what the existing reranker sees; `rerank.enabled: true` and `rerank.min_relevance_score: 0.3` in the shipped graph config. No backend-specific rerank path is added. | Must | Decision 4; one rerank implementation. |
| FR-017 | The build for `graph_hybrid` writes both the property graph (FalkorDB) and a LanceDB chunk index over the same chunks, so the dense half has something to read. The chunk table stays at `retrieval.lancedb_index_dir`; the merge log and the index manifest live in the same directory, which is what the manifest's index-time signature already assumes. | Must | Today a graph build leaves LanceDB empty, so a naive `graph_hybrid` would retrieve from one side only. |

### 6.6 Promotion and deployment

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-018 | `promotion._ALLOWED_BACKENDS` gains `graph_hybrid`; `graph` and `mixed` stay rejected with the existing reason. A `graph_hybrid` candidate is scored and judged by the unchanged rules. | Must | Decision 6. |
| FR-019 | `indexing._ALLOWED_BACKENDS` gains `graph_hybrid` so a promoted champion can be built beside the serving index, and `UnsupportedIndexBackend`'s message names the accepted backends including it. | Must | A gate that accepts a config the workspace build refuses would break the serving index after a promotion. |
| FR-020 | FalkorDB stays out of the five core Compose services and out of the default deployment. `compose.graphdb.yml` keeps the `falkordb` service as the optional host, and the docs say a `graph_hybrid` champion needs it running. | Must | Decision 6. |
| FR-021 | `evaluation/runner._require_query_expansion_llm` treats `graph_hybrid` as an LLM-calling backend, so a retrieval-only eval on it fails naming `LLM_MODEL` instead of erroring per case. | Must | Entity detection is a completion; failing once by name is the existing convention. |

### 6.7 Tests and CI

| Id | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-022 | One integration test builds a small graph on a real FalkorDB instance, then asserts that `graph_hybrid` returns chunks carrying `doc_id` and text from the corpus, and that a chunk reached from both sides appears once. It skips with a clear reason when no store is reachable, unless `DEEPFIX_FALKORDB_REQUIRED=1`, where it fails. | Must | Decision "one integration test", and a local laptop without FalkorDB must still be able to run the suite. |
| FR-023 | CI gains one job that runs that test with a `falkordb/falkordb` service container and `DEEPFIX_FALKORDB_REQUIRED=1`. No existing job changes. | Must | The graph path must not rot again. |
| FR-024 | No narrow unit tests are added for the ontology, the merge predicate, or the score merge; their behaviour is covered by the integration test and by the existing config/skill guards. | Must | Repository rule: meaningful tests only. |

## 7. Non-functional requirements

| Id | Requirement |
| :--- | :--- |
| NFR-001 | **Cost.** A 500-document build with 1024-token chunks is one extraction completion per chunk (roughly 700–900 chunks) plus one embedding per entity, at `graph_num_workers` parallelism. Deduplication embeds each distinct entity name once. |
| NFR-002 | **Latency.** `graph_hybrid` adds one extraction completion per question (entity detection) and one vector query per detected entity. It does not add a completion per entity. |
| NFR-003 | **Security and privacy.** The corpus is public Bundestag material. No secret is printed: the graph build logs no key, and the merge log holds entity names from the corpus only. FalkorDB authentication uses the existing `FALKORDB_PASSWORD`; an empty password keeps the server open on a loopback or Compose-network port, exactly as today. |
| NFR-004 | **Reliability.** A FalkorDB outage fails a graph build or a `graph_hybrid` query with the existing error path; it never silently returns dense-only results, because a silent fallback would make a promoted champion's scores unreproducible. |
| NFR-005 | **Data integrity.** The merge log is append-only for the life of a build directory, and one build writes to its own index directory, so two builds cannot interleave. |
| NFR-006 | **Compatibility.** A config without any new key behaves exactly as today; the `entity_relationship` fallback keeps user-uploaded corpora building. |
| NFR-007 | **Accessibility.** N/A — no user interface changes. |

## 8. Acceptance criteria (release level)

1. `deepfix schema --json` lists the six new retrieval keys with their bounds, and `graph_hybrid` appears in the `RetrievalBackend` enum.
2. A `graph_hybrid` config with `graph_store: none` fails validation with a message naming both keys.
3. `GRAPH_EXTRACTION_SCHEMAS["bundestag"]` carries the nine entity types, nine relations, and the 23 allowed triples of FR-005, and `bundestag` is the value in the shipped graph config.
4. `deepfix index --config configs/graph_hybrid_falkordb.yml` on a FalkorDB host builds a graph, writes `<index-dir>/graph_merges.jsonl`, and reports `graph_merges` in its JSON payload.
5. `deepfix eval --retrieval-only` on that index returns chunks whose `doc_id` values are corpus document ids, and no duplicate `(doc_id, text)` pair.
6. `apply_candidate` scores a `graph_hybrid` candidate and rejects a `graph`
   **and** a `mixed` candidate with the existing reason string.
7. `pytest tests/test_graph_hybrid.py` passes against a FalkorDB host, and the new CI job runs it with `DEEPFIX_FALKORDB_REQUIRED=1`.
8. `ruff check`, `ruff format --check`, the `api`, `cli`, and `web` jobs, and the quickstart workflow are green.

## 9. Dependencies and assumptions

- **llama-index-core 0.14.25** ships `SchemaLLMPathExtractor` with `kg_validation_schema` as `Dict[str, str] | List[Triple]` and `num_workers`; `CustomPGRetriever` is importable from `llama_index.core.indices.property_graph`. Verified in the installed venv.
- **`llama-index-graph-stores-falkordb` 0.7.0** stores chunk nodes as `(c:Chunk {id, text, ...metadata})`, links them with `(:Chunk)-[:MENTIONS]->(:__Entity__)`, and stamps `triplet_source_id` on the entity and relation properties. Verified against a live FalkorDB.
- **FalkorDB has no APOC**, so `apoc.text.distance` and friends are unavailable; deduplication is Python + `rapidfuzz` + the configured embed model. `rapidfuzz` (BSD-3, no transitive cost) is a **new runtime dependency**: it goes in `pyproject.toml` and `uv lock` is regenerated, which `tests/test_lockfile.py` guards.
- **`HF:wirthual/dip-bundestag`** rows have `doc_id` and `text` only. Assumption: the ontology is extracted from the text, not from DIP metadata fields, because the corpus carries none.
- **Assumption:** 500 documents is enough to review extraction quality. If the first build shows the ontology is wrong, the fix is the ontology, not the sample.
- **Assumption:** a `graph_hybrid` champion is acceptable to operate with FalkorDB off the default deployment, because the gate is a decision aid and enabling the store is a deliberate operator step.

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| R-1 Extraction is lossy: the LLM does not emit the identifier forms the ontology expects, so entities never match and the graph is disconnected. | `graph_hybrid` scores no better than `hybrid`, and the work looks wasted. | The document sample makes the first build cheap to inspect; the dedup log and the graph store are both readable; the fallback schema keeps the old path alive. |
| R-2 Deduplication over-merges and silently collapses two different Drucksachen. | Wrong answers with confident citations — the worst failure mode for this corpus. | Two independent signals (FR-009), the digit-difference guard (FR-010), a JSONL log for every merge (FR-011), and a 500-document reviewable default. |
| R-3 A promoted `graph_hybrid` champion breaks on a deployment without FalkorDB. | Serving errors after a promotion. | FR-019 makes the workspace build accept the backend so the failure is a build error with a message, not a query-time surprise; FR-020 documents the host requirement; the default deployment is unchanged. |
| R-4 The graph cannot be updated incrementally, so a document added in the workspace needs a full graph rebuild. | Slow first build after an upload. | Out of scope by design (§4), stated here and in the PRD's scope so nobody discovers it in production. |
| R-5 Entity detection adds a completion per question on every retrieval, including retrieval-only evals. | Eval cost and latency. | One completion per question (NFR-002), a bounded entity list (`graph_query_entity_limit`), and the existing expansion cache pattern is not needed because there is exactly one call. |
| R-6 FalkorDB's vector index is label-scoped and dimension-bound; a mixed embedding dimension disables it. | Silent full scans, slow queries. | The existing store code warns on a dimension change; one embedding model per deployment is already the rule (NFR-001). |

## 11. Traceability

| FR | Goal / decision it serves |
| :--- | :--- |
| FR-001 | Goal 1 (ontology selectable), decision 1 |
| FR-002 | Goal 4, Goal 7 (levers in config) |
| FR-003 | Goal 4, decision 4 |
| FR-004, FR-005, FR-006 | Goal 1, decision 2 |
| FR-007, FR-008 | Goal 1, decisions 2 and 5 |
| FR-009, FR-010, FR-011, FR-012 | Goal 3, decision 7 |
| FR-013, FR-014, FR-015, FR-016 | Goal 2, decision 4 |
| FR-017 | Goal 2 (dense half must exist) |
| FR-018, FR-019, FR-020 | Goal 4, decision 6 |
| FR-021 | Goal 2 (a retrieval-only eval must not error per case) |
| FR-022, FR-023, FR-024 | Goal 5 |

## 12. Decisions (settled by Fadel, 2026-11-17)

The create-prd skill asks three to five questions first. These were asked and
answered before the draft; they are decisions, not open questions.

1. **Which corpus does the ontology target?**
   A. The Bundestag DIP corpus, with the generic schema kept as a fallback for
   user uploads, selected by the existing `graph_extraction_schema` key.
   B. A generic ontology for every corpus.
   Answer: **A.**
2. **How strict is extraction?**
   A. `strict=True` with a `kg_validation_schema` triple list that allows each
   relation only between specific types, in the triple-list form the installed
   llama-index supports.
   B. Strict vocabulary, no pair restrictions.
   Answer: **A.**
3. **Which graph store?**
   A. FalkorDB, already wired, with deduplication in Python because there is no
   APOC.
   B. Memgraph, whose APOC makes server-side deduplication possible.
   Answer: **A.**
4. **How does the graph reach an answer?**
   A. A new `graph_hybrid`: LLM entity detection, `VectorContextRetriever` per
   entity, one to `graph_traversal_hops` hops, linked chunks merged with dense
   retrieval and reranked. Citable chunks only.
   B. A new retriever that answers from triplet text.
   Answer: **A.**
5. **Which model extracts, and how much?**
   A. The existing `LLM_MODEL`, with `num_workers` configurable and a document
   sample whose shipped default is 500.
   B. A dedicated extraction model and the full corpus.
   Answer: **A.**
6. **May the gate promote it?**
   A. Yes, under the unchanged rules; plain `graph` stays rejected; FalkorDB
   stays optional and off in the default deployment.
   B. No, keep it manual.
   Answer: **A.**
7. **How is deduplication reviewed?**
   A. A separate build step, embedding cosine ≥ 0.9 **and** a `rapidfuzz` name
   match, every merge logged to JSONL, identifiers never merged.
   B. Automatic merging with no log.
   Answer: **A.**

## 13. Glossary

- **Drucksache** — a printed paper of the Bundestag: a bill, motion, or inquiry.
- **Vorgang** — the legislative procedure a Drucksache belongs to; the unit DIP
  tracks across steps.
- **Plenarprotokoll** — the verbatim record of a sitting.
- **Fraktion** — a parliamentary group.
- **Ausschuss** — a committee.
- **kg_validation_schema** — the allow-list of `(subject type, relation, object
  type)` triples that `SchemaLLMPathExtractor` enforces in strict mode.
- **triplet source chunk** — the chunk a triplet was extracted from, recorded as
  `triplet_source_id` on the entity and relation properties.
- **canonical entity** — the node a merge keeps; the others become aliases.

## 14. Open questions

None blocking. Two operational questions are recorded for the implementation
notes:

1. **How many chunks does a 500-document build actually produce, and therefore
   how long does it take?** Owner: the first real build. The PRD's estimate
   (700–900 chunks at 1024 tokens) is arithmetic on the corpus, not a
   measurement.
2. **Does the extraction prompt need per-type hints (e.g. "a Drucksache is named
   `BT-Drucksache <Wahlperiode>/<Nummer>`")?** Owner: the reviewer of the first
   build. A prompt change is a schema-entry change, not a new config key.

# GRAPH-003 — Entity deduplication as a logged post-extraction step

## Type

Feature

## Status

Draft

## Summary

After a graph is extracted, entities of the same label that name the same thing
are merged into one node, relationships and chunk mentions are re-pointed onto
the survivor, and every merge is written to a JSONL log an operator can review.
Two entities merge only when their name embeddings are close **and** their names
are close, and two names that differ only in an identifier are never merged — so
`BT-Drucksache 20/123` and `BT-Drucksache 20/124` stay apart while
`BT-Drucksache 20/123` and `Drucksache 20/123` can merge.

## PRD traceability

- Related: FR-002 (the three dedup keys), FR-009, FR-010, FR-011, FR-012
- Goals: Goal 3; decision 7
- Depends on: GRAPH-002 (there must be a graph to dedupe)

## Acceptance criteria

1. Given a graph build finishes extraction, when the build completes, then a
   deduplication step has run and the `--json` payload carries
   `"graph_merges": <int>`, the number of entities merged away.
2. Given two entities of the same label whose name embeddings have cosine
   similarity ≥ `retrieval.graph_dedup_similarity` (default `0.9`) **and** whose
   names satisfy one name test, when deduplication runs, then they are one node.
3. The name test passes when either the `rapidfuzz` partial ratio of the
   lower-cased names is ≥ `retrieval.graph_dedup_name_ratio` (default `0.9`) with
   one name containing the other, or the Levenshtein distance of the lower-cased
   names is ≤ `retrieval.graph_dedup_max_edit_distance` (default `2`).
4. Given two names whose identifier tokens differ, when deduplication runs, then
   they are never merged regardless of similarity. The guard compares both the
   sorted tuple of digit runs and the trailing alphanumeric token, so
   `BT-Drucksache 20/123` vs `BT-Drucksache 20/124`, `§ 315c` vs `§ 315d`, and
   `2023` vs `2024` each stay two nodes.
5. Given two entities of **different** labels, when their names and embeddings
   are identical, then they are never merged.
6. Given three entities where A merges with B and B merges with C, when
   deduplication runs, then all three become one node (transitive union), and the
   canonical name is the longest **original** name in the cluster, ties broken by
   `sorted()` order of the lower-cased names.
7. Given a merged duplicate, when the merge completes, then every relationship
   that pointed at or from the duplicate now points at or from the canonical
   node — checked with `MATCH (e:__Entity__)-[r]->(o) RETURN type(r)`, read from
   both endpoints — every `(:Chunk)-[:MENTIONS]->` edge on the duplicate now
   targets the canonical node, the duplicate's name is present in the canonical
   node's `aliases` property, and the duplicate node no longer exists.
8. Every merge appends exactly one JSON object to
   `<index_dir>/graph_merges.jsonl` with the keys `label`, `canonical`, `merged`,
   `similarity`, `name_ratio`, `edit_distance`, and `reason`; the file is created
   on the first merge and is not rewritten by a later build into the same
   directory.
9. Given a build in which nothing merges, then `graph_merges` is `0` and the log
   file exists and is empty, so
   `payload["graph_merges"] == 0 and log.exists() and log.read_text() == ""`
   holds.
10. The merge runs through the graph store's `structured_query` only; the module
    imports no Memgraph- or FalkorDB-specific class and calls no APOC procedure.
11. `tests/test_graph_hybrid.py` contains one case over a corpus holding a true
    duplicate pair and an identifier pair, and it asserts that only the true
    duplicate merged, that the log lists exactly that merge, and that both
    identifier entities still exist as separate nodes.

## Out of scope

- Deduplicating across labels (a `PERSON` "Merkel" and a `THEMA` "Merkel").
- An LLM-based entity resolver, or matching against DIP person ids.
- A CLI command to re-run deduplication on an existing graph.
- Merging during extraction.

## Notes

FalkorDB has no APOC, so the blog post's single Cypher statement (which uses
`apoc.coll.*` and `apoc.text.distance`) cannot run. The equivalent is: read the
candidate pairs with one query per label, decide in Python with `rapidfuzz` and
the configured embed model, then rewrite with `MERGE`/`SET`/`DETACH DELETE`.

`rapidfuzz` is a new runtime dependency. `pyproject.toml` gains it, `uv lock` is
regenerated, and `tests/test_lockfile.py` (which asserts every declared
requirement is locked) must pass.

The identifier guard is deliberately strict: it compares the sorted tuple of
digit runs in each name **and** the trailing alphanumeric token of each name. If
either differs, the pair cannot merge. The trailing token is what separates
`§ 315c` from `§ 315d`, whose digit runs are identical. A name with no digits and
no trailing identifier is compared on embeddings and letters alone, which is what
lets `Ausschuss für auswärtige Angelegenheiten` merge with
`Auswärtiger Ausschuss`.

Embeddings come from `_get_embed_model()`, one call per distinct
`(label, lower-cased name)`, so a graph with 4,000 distinct entity names pays for
exactly 4,000 short embeddings and never re-embeds a name it has seen.

## Definition of done

- [ ] Code complete
- [ ] `tests/test_graph_hybrid.py` case passing against a FalkorDB host
- [ ] `pyproject.toml` and `uv.lock` updated; `tests/test_lockfile.py` passes
- [ ] `uv run ruff check` and `uv run ruff format --check` clean

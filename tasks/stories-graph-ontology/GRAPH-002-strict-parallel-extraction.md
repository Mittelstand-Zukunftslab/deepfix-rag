# GRAPH-002 — Strict, parallel extraction over a document sample

## Type

Feature

## Status

Draft

## Summary

A graph build extracts against the selected schema's pair allow-list in strict
mode, with a configurable number of parallel workers, and can be limited to the
first N documents so a first build is cheap enough to review. Both levers are
config keys, so an operator changes them in YAML and an HPO trial could sample
them later without a code change.

## PRD traceability

- Related: FR-002 (`graph_num_workers`, `graph_build_document_limit`), FR-007,
  FR-008
- Goals: Goal 1; decisions 2 and 5
- Depends on: GRAPH-001 (the schema's `validation_triples` is what strict mode
  enforces)

## Acceptance criteria

1. Given a graph config, when the extractor is constructed, then it is a
   `SchemaLLMPathExtractor` with `strict=True`,
   `kg_validation_schema=<the selected schema's triples>`,
   `num_workers=retrieval.graph_num_workers`, and
   `max_triplets_per_chunk=retrieval.graph_max_paths_per_chunk`.
2. Given a schema whose `validation_triples` is empty, when the extractor is
   constructed, then `kg_validation_schema` is `None` and `strict` is still
   `True`.
3. Given `retrieval.graph_num_workers` unset, when a config is loaded, then it
   is `4`; a value of `0` or `17` is rejected by validation with the field name
   in the message; `1` and `16` are accepted.
4. Given `retrieval.graph_build_document_limit` unset, when a config is loaded,
   then it is `null` and every loaded document is extracted, exactly as today.
5. Given `retrieval.graph_build_document_limit: 500` and a corpus of 900
   documents, when the build runs, then exactly the first 500 loaded documents
   are handed to the extractor, and the build metadata's `document_count` is the
   sampled number, `500` — not the corpus size.
6. Given the same limit, when the build writes its `--json` payload, then it
   carries `"graph_build_document_limit": 500` alongside the existing fields, so
   a reviewer can see the build was sampled.
7. Given a graph build, when it extracts, then the LLM used is the pipeline's
   `_get_llm()` — the same `LLM_MODEL`, base URL, and key as generation — and no
   new setting is read.
8. Both keys appear in `deepfix schema --json` under the retrieval fields with
   their bounds, and both are treated as index-time: changing either makes
   `index_time_signature` differ, so a later `eval` on the old index exits 1 with
   `error_type: stale_index`.

## Out of scope

- Choosing the sample by anything other than "the first N the loader returns".
  A stratified or random sample is a different change with its own seed problem.
- Resumable extraction across runs.
- Per-document extraction progress in the API's index status.

## Notes

`graph_build_document_limit` is applied where the documents are already a list —
in `_build_index`'s graph branch — not in the loader, so `data.limit` (which
limits the *corpus* the index is built from and is part of the corpus hash) keeps
its current meaning. A build with `data.limit: 500` and
`graph_build_document_limit: null` builds a graph over all 500 loaded documents;
the two levers compose without interacting.

Both keys go into `index_time_signature` and `index_time_key_names()`. The
signature list is asserted wholesale by
`tests/test_skills.py::test_index_time_keys_are_listed_in_every_pathology_skill`,
so the two pathology skills that enumerate index-time keys must list them in the
same commit.

## Definition of done

- [ ] Code complete
- [ ] No new test module; the behaviour is covered by GRAPH-004's end-to-end case
      and by `tests/test_harness.py`'s existing `schema --json` guard
- [ ] `uv run ruff check` and `uv run ruff format --check` clean
- [ ] The two `INDEX_TIME_SKILLS` texts list the new keys

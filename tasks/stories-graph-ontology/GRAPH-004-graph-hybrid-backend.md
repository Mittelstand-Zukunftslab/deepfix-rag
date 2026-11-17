# GRAPH-004 — The `graph_hybrid` backend: entity retrieval merged with dense

## Type

Feature

## Status

Draft

## Summary

A config with `retrieval.backend: graph_hybrid` answers a question by asking the
model which entities the question names, retrieving the graph neighbourhood of
each of them, and merging those chunks with the chunks dense LanceDB retrieval
finds. The result is reranked by the existing reranker and every passage is a
real chunk carrying `doc_id`, so Ask renders citations exactly as it does for a
`hybrid` champion.

## PRD traceability

- Related: FR-003, FR-002 (`graph_query_entity_limit`), FR-013, FR-014, FR-015,
  FR-016, FR-017, FR-021
- Goals: Goal 2; decision 4
- Depends on: GRAPH-002 (a graph built with the ontology must exist)

## Acceptance criteria

1. Given a config with `backend: graph_hybrid`, when it is validated, then it
   loads; given the same config with `graph_store: none`, validation fails with a
   message naming both `backend` and `graph_store`.
2. Given a `graph_hybrid` query, when it runs, then exactly one completion is
   made to detect entities in the question, regardless of how many entities it
   names.
3. Given a question naming three entities and `graph_query_entity_limit: 5`,
   then the `VectorContextRetriever` is invoked once per entity; given a question
   naming nine entities, then it is invoked at most `graph_query_entity_limit`
   times.
4. Given a question naming no entity, when it runs, then the
   `VectorContextRetriever` is invoked once on the raw question and the graph side
   still contributes candidates.
5. Given each graph pass, when it retrieves, then `path_depth` is
   `retrieval.graph_traversal_hops` and the returned nodes are **chunks**: each
   node's text is the chunk text from the corpus, so no returned text matches
   `^\S+ -> \S+ -> \S+$`, and its metadata carries the `doc_id` of the document it
   came from.
6. Given a triplet whose source chunk cannot be loaded from the graph store,
   then that triplet contributes nothing and the query still succeeds.
7. Given both sides return the same chunk, when the lists merge, then it appears
   once, with the higher of the two scores.
8. Given `dense_weight: 0.5` and `graph_weight: 0.5`, the merged list is the
   weighted union; given both weights `0`, the two sides count equally; given
   `dense_weight: 1` and `graph_weight: 0`, only dense candidates survive.
9. The merged list is cut to `retrieval.top_k` before it reaches the reranker,
   and the reranker is the existing `_get_reranker()` — no `graph_hybrid` branch
   in `rerank.py`.
10. Given `rerank.enabled: true` and `rerank.min_relevance_score: 0.3`, a merged
    passage scoring below 0.3 is dropped exactly as it is for `hybrid`, and
    `Ask`/`Search` see the same `RetrievalResult` shape.
11. Given a `graph_hybrid` build, when it completes, then the LanceDB table at
    `retrieval.lancedb_index_dir` is non-empty and holds the same chunks the
    graph holds, so the dense side of the same config retrieves from its own
    build. An empty table would silently degrade `graph_hybrid` to graph-only.
12. Given `backend: graph_hybrid` and `retrieval_only: true` with
    `LLM_MODEL` unset, then the eval fails once before scoring any case, naming
    `LLM_MODEL`. The check lives in
    `EvaluationRunner._require_query_expansion_llm`, which gains `graph_hybrid`
    to the set of LLM-calling backends.
13. `backend: graph` behaves exactly as it does today: `LLMSynonymRetriever` plus
    `VectorContextRetriever` through `index.as_retriever`. No branch is
    rewritten, and `tests/test_index_creation.py`'s existing FalkorDB `graph`
    case still passes.
14. `tests/test_graph_hybrid.py` contains one case that builds a graph from a
    small fixture corpus, runs a `graph_hybrid` query, and asserts ACs 5, 7, and
    9: chunk text with `doc_id`, one copy of a doubly-retrieved chunk, and a
    result list no longer than `top_k`.
15. Given a graph store that cannot be reached, when a `graph_hybrid` query runs,
    then the query raises; it never returns dense-only results, because a silent
    fallback would make a promoted champion's scores unreproducible.

## Out of scope

- BM25 in the `graph_hybrid` mix. Two sides is the decision; a third would need
  its own weight.
- Reranking the graph candidates separately.
- Caching detected entities across questions.
- Changing the `graph` or `mixed` retrievers.
- Ask, Search, or vote changes. The contract "a result is a chunk with
  `doc_id`" is preserved, not extended.

## Notes

`CustomPGRetriever` is the extension point: it is the class the LlamaIndex blog
post subclasses, it is in `llama_index.core.indices.property_graph`, and its
`init`/`custom_retrieve` pair keeps the graph-store plumbing in the base class.
Its `custom_retrieve` may return `NodeWithScore` objects, which is how a chunk
keeps its metadata and score instead of collapsing to a string.

The dense side is the same `VectorStoreIndex.from_vector_store(...).as_retriever`
call `_get_dense_retriever` already makes; `graph_hybrid` reuses it rather than
re-implementing it. The merge is a small function: min-max normalise each side to
`[0, 1]`, multiply by that side's weight, union by `(doc_id, text)`, keep the max,
sort, cut to `top_k`. Min-max rather than raw scores because a graph similarity
and a LanceDB cosine distance are not on the same scale — the same reason the
existing `relative_score` fusion exists.

The entity-detection call is a `structured_predict` against a two-field Pydantic
model (`names: list[str]`), the same technique as the blog post's
`OpenAIPydanticProgram` but through the LLM the pipeline already holds, so no new
program abstraction is introduced.

`_build_retriever` gains one `elif`; `_build_index` gains a branch that, for
`graph_hybrid`, builds the property graph and then also writes the chunk nodes
into LanceDB. The graph store already upserts chunks; the LanceDB write is the
same `VectorStoreIndex(nodes, ...)` call the non-graph branch makes.

## Definition of done

- [ ] Code complete
- [ ] `tests/test_graph_hybrid.py` case passing against a FalkorDB host
- [ ] `uv run ruff check` and `uv run ruff format --check` clean
- [ ] `graph` and `mixed` behaviour unchanged (the existing `graph` test still
      passes)

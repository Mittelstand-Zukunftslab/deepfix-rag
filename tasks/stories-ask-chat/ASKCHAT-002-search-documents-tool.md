# ASKCHAT-002 — Give the model one read-only tool over the champion index

## Type
Feature

## Status
Draft

## Summary
The model gets exactly one callable, `search_documents`, which returns the ranked passages an answer may be built from. It reads the same serving index and the same `rerank.min_relevance_score` floor that `POST /retrieval` and `POST /ask` already apply, so the evidence the chat shows is the evidence the optimizer scores. When nothing clears the floor the tool says so instead of handing back five unrelated chunks.

## PRD traceability
- Related: FR-004, FR-023, FR-024
- Decisions: D-2 (one tool, always runs, returns the floor's survivors), D-3 (consumes ASK-001's `Grounding`), D-6 (served retrieval is scored retrieval), D-7 (no model setting)
- NFR touchpoints: NFR-1 (indexed reads), NFR-4 (read-only, no write path), NFR-3 (bounded depth)

## Dependencies
- Requires: ASK-001 (`Grounding`, the `no_relevant_sources` value) and ASK-002 on the branch — `rerank.min_relevance_score` in `RAGConfig`, `RAGWorkflow._ranker_split` / `split_by_relevance_floor`, and `RAGResult.dropped_results` are already there at `223a122`.
- Blocks: ASKCHAT-003.

## Acceptance criteria

**The declaration**

1. Given `src/deepfix_rag/ask_chat.py`, when it is read, then `search_documents` is declared once, as a JSON schema with the D-2 shape — input `{query: string 1..500, top_k?: integer}` and output `{query, grounding, passages[], max_score}` — and the OpenAI-style function definition the loop sends is built from that same object, so there is one source and not two.
2. Given a `query` longer than 500 characters or empty, when the tool validates its input, then it refuses before any retrieval runs.

**What it reads**

3. Given a serving index, when `search_documents` runs, then it retrieves through `QueryService.retrieve` under an `IndexCoordinator.checkout()` lease, records the lease's `build_id` for the turn, and releases the lease in a `finally` so a raised error cannot pin a build that is being replaced.
4. Given `rerank.enabled: true` and `rerank.min_relevance_score: 0.2`, when the tool returns, then every passage in `passages` scored at or above 0.2, and no passage below it can appear. This is not a second filter: `QueryService.retrieve` already applies the floor through `_search` → `_search_split` → `RAGWorkflow._ranker_split`, so the tool receives survivors only. The tool must **not** re-implement the comparison, and must not import `_ranker_split` to redo it on an already-filtered list.
5. Given no passage clears the floor, when the tool returns, then `grounding` is `"no_relevant_sources"`, `passages` is `[]`, `max_score` is 0, and no passage text is returned at all. The empty list is what `QueryService.retrieve` hands back in that case; the tool reports it rather than treating an empty result as an error.
6. Given `rerank.enabled: false`, when the tool returns, then the floor does not apply — `_ranker_split` returns the nodes unchanged when `min_relevance_score <= 0` or rerank did not run, because the floor is calibrated on the reranker's `relevance_score` and a fused score is a different scale, measured near 0.03 on the sample fixture. The result is therefore the `displayed_limit(config, MAX_DISPLAYED_HITS)` window, the same list the Search page shows, and the tool must never offer the model 50 passages.
7. Given a requested `top_k`, when it is outside `[1, displayed_limit(config, MAX_DISPLAYED_HITS)]`, then it is clamped into that range rather than refused, and the response's `query` echoes what was actually searched.
8. Given a passage whose text exceeds `ASK_CHAT_MAX_PASSAGE_CHARS`, when it is returned, then its text is truncated to that length and `truncated` is `true`. Given a shorter passage, `truncated` is `false`.
9. Given each passage, when it is returned, then it carries `rank` (1-based, in returned order), `doc_id`, `filename` resolved through `documents.document_filenames`, `text`, `score`, and `truncated`.

**Served equals scored**

10. Given the real sample index (`populated_sample_index_dir` with `MockEmbedding`), when the parity test runs, then for each query the tool's `(doc_id, rank, score)` triples equal `QueryService.retrieve(query)` truncated to `displayed_limit` — same ids, same order, same scores, no rerun, no reordering. This is FR-024 and it must fail loudly if anyone "improves" the tool's retrieval.
11. Given `rerank.min_relevance_score` raised above every score in the sample index, when the parity test runs the same query, then both the tool and `QueryService.retrieve` return `[]`. That equality is the point: the floor is inside the retrieval path both call, so there is no state where the tool shows passages the product's own search would not. A test that instead asserted the tool returns `[]` while `retrieve` returned passages would be asserting a divergence this story exists to prevent.
12. Given `deepfix eval` and `promotion.apply_candidate`, when this story lands, then neither is modified, and `index_time_signature` still ignores every `rerank.*` key, so a candidate that only moves the floor reuses the champion's index.

**No write path**

13. Given the tool's implementation, when it is read, then it performs no insert, update, or delete, and it cannot reach `configs/champion.yaml`, `indexes/current`, or holdout. FR-023 is a property of the whole feature and this is where it is easiest to break.

## Out of scope
- A second tool. A `list_documents` or `get_document` would make the loop browse, at the cost of a second retrieval contract and a prompt budget nothing bounds.
- Query rewriting, expansion, or a second model call to reformulate the query. The model writes the tool's `query` itself (D-2, FR-012).
- Recalibrating the floor, changing `COHERE_MODEL`, or normalising scores per provider. ASK-002 settled the floor at 0.2 with measurements.
- Any model call. This story produces a tool; ASKCHAT-003 loops over it.

## Notes
- The tool's depth must come from `displayed_limit` in `src/deepfix_rag/api/routes/retrieval.py` rather than from `rerank.top_n` directly, because that helper is what the product already uses to decide what a member is shown. Two definitions of "how many passages" would drift.
- `QueryService.retrieve` already applies the floor through `_search_split`, so this story does not filter anything. Its job is to report what came back — including `no_relevant_sources` when nothing did — and to bound the depth, which `QueryService.retrieve` does not do because `POST /retrieval` truncates with `displayed_limit` at the route.
- `max_score` is the highest surviving passage's score, or 0. It is what makes a low-confidence turn visible in the trace without re-reading every passage.
- The `MockEmbedding` fixture scores deterministically, so the parity test is stable; it asserts ids and order, not an absolute score threshold, so it does not become a second calibration test.
- ASK-002's `_ranker_split` returns the nodes unchanged when `min_relevance_score <= 0.0`, which is also the documented way to disable the floor. Do not add a separate "floor disabled" flag.

## Definition of done
- [ ] Code complete
- [ ] The parity test (AC 10) and the floor-does-not-touch-the-index test (AC 11) green on the real sample index
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run --env-file .env pytest -m "not network and not e2e"` pass
- [ ] Committed on its own before ASKCHAT-003 starts

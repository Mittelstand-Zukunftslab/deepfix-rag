# ASK-002 — Drop passages that are not relevant to the question

## Type
Bug

## Status
Draft

## Summary
A question the corpus cannot answer no longer comes back with five unrelated passages attached. The
retrieval path applies a configurable relevance floor, so a question whose best passage scores below
it skips generation and answers **Not found in the documents** with no sources shown. The passage
scores stay on the stored interaction, so an operator can still see what was retrieved.

## PRD traceability
- Related: FR-9 (the interaction keeps its hit snapshot), FR-022 (the Ask screen), US-010, US-001
- Decisions: the floor is `rerank.min_relevance_score` in `RAGConfig`, so the optimizer can tune it
- Out-of-PRD: none. This closes the review's "no relevance cutoff on the live path".

## Dependencies
- Requires: ASK-001 (the `no_relevant_sources` grounding value and the honest UI state).
- Blocks: nothing.

## Acceptance criteria

1. Given `RAGConfig`, when the schema is read, then `rerank` has a
   `min_relevance_score: float` field with a documented default and bounds `0.0 <= x <= 1.0`, it is a
   config key and not an environment setting, and `configs/champion.yaml` sets it explicitly so a
   tuner can raise or lower it with `deepfix mutate-config --set rerank.min_relevance_score=…`.
2. Given rerank is enabled, when retrieval returns candidates, then a candidate whose rerank
   relevance score is below `rerank.min_relevance_score` is dropped before the passages are returned
   or sent to the model, and the passages that survive keep their relative order.
3. Given rerank is disabled, when retrieval returns candidates, then the floor is applied to the
   retriever's own score for that candidate, using the same config value, and a candidate below the
   floor is dropped there too, with a test covering the rerank-off case.
4. Given no candidate passes the floor, when `POST /ask` handles the question, then it does not call
   the generation model, the response has `grounding == "no_relevant_sources"`, `answer` is a short
   honest sentence ("I did not find anything about that in these documents."), `error` is null, and
   `hits` is empty so the page shows no sources.
5. Given that same request, when the stored interaction is read, then the dropped candidates are
   still recorded with their scores for debugging and for the admin History screen, so an operator can
   tell "nothing was retrieved" from "everything was below the floor".
6. Given an in-corpus question whose passages pass the floor, when `POST /ask` handles it, then the
   answer is generated and `grounding` is `"grounded"` exactly as before — the default must not drop
   answers for normal questions.
7. Given the default value in `champion.yaml`, when the commit message and the pull request body are
   read, then they name the score scale the default was measured on, the evidence used, and the risk
   if that scale does not transfer to the deployed reranker.
8. Given the eval path, when a config is scored with `--retrieval-only` or with generation, then it
   sees the same floor, so a champion tuned against `recall`/`ndcg` is scored on the passages the live
   path would actually use.
9. Given `apply_candidate`, when a candidate sets the floor, then the promotion rules are unchanged:
   `index_time_signature` still ignores `rerank.*` (so no re-index), and the dev/holdout gate reads the
   same fields it did before.
10. Given the required end-to-end case, when an off-corpus question is asked against a populated
    index, then no generation call is made and the response is `no_relevant_sources` with no hits.

## Out of scope
- A second, separate floor for retrieval-only searches. `POST /retrieval` returns what it returns;
  the story applies the floor where passages reach an answer, and AC 8 keeps eval consistent.
- Recalibrating the reranker's absolute scale, changing `COHERE_MODEL`, or adding a provider-specific
  normalisation layer.
- Dropping passages at display time only. The floor must also keep them from the model.

## Notes
- The reranker returns at most `rerank.top_n` nodes with `relevance_score` already on each node's
  `score`, so the filter runs immediately after `postprocess_nodes`.
- An empty candidate list must not reach the synthesizer. `QueryService.answer` is the single place
  that decides, so both `/ask` and eval inherit it.
- `error` stays null on the `no_relevant_sources` path: nothing failed, so `GENERATION_FAILED` would be
  a lie and the UI would draw an error where it should draw an empty result.
- The response's `hits` is built from the stored interaction; keeping the dropped rows stored while
  returning an empty `hits` list means the route selects which stored rows to expose, rather than
  writing fewer of them.

## Definition of done
- [ ] Code complete
- [ ] Default chosen from evidence and written into `champion.yaml`
- [ ] Tests added for the off-corpus and in-corpus cases
- [ ] `uv run ruff check`, `uv run ruff format --check`, full `pytest -m "not network and not e2e"`,
      `bun run typecheck`, `bun run lint`, `bun run test` green

---
name: query-rewriting
description: >
  Use when retrieval_miss persists because the question's wording does not match the
  corpus: acronyms, paraphrases, or a domain term the document never uses. This is the
  real query-expansion lever. Set retrieval.query_expansion_num to 2 or 3 so
  LlamaIndex's QueryFusionRetriever asks the generation model for extra paraphrases and
  retrieves with all of them. In an API-driven round the sweep samples it; this skill
  explains when that is worth the completions. It does not add HyDE.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "retrieval_miss is high while recall is low, and the misses read as vocabulary mismatch rather than missing content"
  - "Queries use acronyms, paraphrases or synonyms that the corpus expresses differently"
  - "The hpo-search sweep drew retrieval.query_expansion_num 1 and the trial ranking never separated the counts"
cli_actions:
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.query_expansion_num=2 --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json"
---

**In an API-driven round, this lever is sampled by `hpo-search`, not hand-set here.** The shipped small space samples `retrieval.query_expansion_num` 1, 2 and 3 inside the one bounded `deepfix hpo` sweep, and the sweep caches each paraphrase across all twelve trials. This skill exists to say *when* an expanded config is worth recommending and how the mechanism works; a round does not hand-write an expansion candidate instead of the sweep.

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 40-turn budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Expansion is query-time: do not re-index.

## What the lever actually does

`retrieval.query_expansion_num` is `QueryFusionRetriever`'s `num_queries`, and the pipeline already builds its hybrid and mixed retrievers with it (`src/deepfix_rag/pipeline.py`, `_build_retriever`). At `1` the retriever runs the question as written. At `k` above 1 it makes **one** completion with `retrieval.query_gen_prompt`, asking for `k - 1` paraphrases, then retrieves with those as well as the original and fuses the result sets with `retrieval.fusion_mode`.

So the lever changes *what is retrieved*, not how it is ranked. That is why it helps recall on a vocabulary mismatch and does nothing for a ranking problem. `retrieval.query_gen_prompt` is the template; leave it at its default in a round.

What this is not:

- Not HyDE. Nothing is generated as a hypothetical *document* and embedded.
- Not a rewrite of the query text. The original question is always one of the retrieved query variants.
- Not free. Every expanded question is one completion. See "Cost" below.

## When it is worth it

Take this lever when the misses look like wording, not missing content:

- `retrieval_miss` is the top failure mode and `recall` is low (the sweep's routing rows 3 and 5 put you here).
- The questions use a term the corpus expresses differently — an acronym expanded in the source, a paraphrase, a synonym (`Regelleistung` vs `Regelsatz`, `Haftung` vs `Halterhaftung`).
- `rerank` and fusion have already been measured and did not close the gap: those reorder what was retrieved, and a document that was never retrieved cannot be reranked up.
- Dense and BM25 disagree in a way that reads as vocabulary rather than as a broken index.

Do **not** take it when:

- `recall` is already high and only `ndcg` is weak. That is a ranking problem; use `rerank-calibration` or the fusion modes in `hpo-search`.
- The passages are cut mid-concept. That is `chunking-tuning`, a re-index path.
- `errors > 0` at baseline. Fix that first; an errored case is not a miss.
- The corpus is small enough that the extra paraphrases only surface the same documents. Expansion cannot add a document the index does not hold.

## Cost

One completion per question per requested count. The pipeline caches the completion keyed on the question and the count (`src/deepfix_rag/query_expansion.py`), and the cache spans a whole HPO study and a whole `eval --compare`:

- A 12-trial sweep on 20 questions pays at most `20 x 2 = 40` completions, because count 1 never calls the model and a question is expanded once per distinct count above 1 (2 and 3), not once per trial.
- The full-dev confirmation pays one completion per dev question for the candidate side, and the champion's side is served from the same cache when it expands at the same count.
- `expansion_llm_calls` / `expansion_cache_hits` in the HPO JSON and per trial row report what was actually spent.

The gate re-scores candidate and champion through one cache on purpose: if the two sides were expanded independently, the paired delta would compare two different rewrites of the same question.

## Procedure in a round

1. The sweep is the hypothesis. Run it exactly as `hpo-search` says:

```
deepfix hpo --config <run_dir>/input/hpo_retrieval_small.yaml --base-config <run_dir>/input/champion.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --run-dir <run_dir> --json
```

2. Read `best_trial.params["retrieval.query_expansion_num"]`. If it is 1, the sweep found no gain from expansion on this slice: report that and stop rather than forcing an expanded candidate.
3. If it is 2 or 3, the candidate already carries it. Confirm on the full dev set and gate it exactly as `hpo-search` describes. Do not run a second sweep or hand-tune the count further.

## Outside a round (manual use)

A single-setting check, for a developer reading a failure by hand:

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.query_expansion_num=2 --json
```

```
deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json
```

Read `delta.recall` and `delta.ndcg` together. Expansion that lifts recall and drops nDCG means the extra paraphrases brought back relevant but poorly ordered passages; the fusion mode and rerank are then the next lever.

## Valid values

`retrieval.query_expansion_num`: `0` to `8`. `0` and `1` are both "no expansion". The shipped sweep samples `1`, `2`, `3`; a manual study may go higher, at one completion per question per count.

`retrieval.query_gen_prompt`: the template, with `{num_queries}` and `{query}`. Leave it at the default in a round.

`retrieval.backend` must be `hybrid` or `mixed`: only those build the fusion retriever that expands. On `dense` or `bm25` the value is inert.

## Index-time vs query-time

Expansion is query-time. The index-time keys (`indexing.index_time_signature`) are `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, `retrieval.bit_width`, `EMBEDDING_MODEL`, `corpus_sha256`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, `retrieval.graph_max_paths_per_chunk`, `retrieval.graph_num_workers`, `retrieval.graph_build_document_limit`, `retrieval.graph_dedup_similarity`, `retrieval.graph_dedup_name_ratio`, and `retrieval.graph_dedup_max_edit_distance`. This skill changes none of them, so the candidate reuses the champion's index and both evals read `<run_dir>/idx-cache`.

## Pitfalls

- Setting expansion on a `dense` or `bm25` champion. There is no fusion retriever, so nothing expands.
- Reading an expanded candidate's nDCG gain as a ranking improvement. Expansion changed the candidates; that is a retrieval win.
- Hand-setting expansion in a round instead of letting the sweep sample it. Without the sweep's shared cache and 20-question subsample there is no cheap way to compare counts.
- Expecting expansion to fix a mid-concept passage split. The gold document is already retrieved; use `chunking-tuning`.
- Reporting `expansion_llm_calls` as if it were a score. It is the bill.

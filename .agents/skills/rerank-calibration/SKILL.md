---
name: rerank-calibration
description: >
  Use when eval shows high recall but weak ndcg: the relevant documents are retrieved
  and then ranked too low. Turn on rerank.enabled and raise retrieval.top_k.
  There is no rerank score-threshold field.
failure_mode:
  - retrieval_induced_error
trigger_conditions:
  - "recall >= 0.8 and ndcg < recall - 0.15 on eval --json (optimization-loop routing row 3)"
  - "Summary recall is high while summary ndcg is low; no per-row data is needed"
cli_actions:
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 40-turn budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Re-ranking is query-time: do not re-index for `rerank.*` or `retrieval.top_k`, and pass `--index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache` so both sides read the local copy of the champion index.

**In an API-driven round, rerank is searched through `hpo-search`, not hand-set here.** `hpo-search` runs one bounded `deepfix hpo` sweep over the shipped small space, which samples `rerank.enabled`, `rerank.top_n`, `retrieval.top_k` and the fusion settings together, then confirms the best trial on the full dev set. This skill keeps the single-setting recipe and the validated findings for manual use; a round does not hand-write one rerank candidate instead of the sweep.

The rerank settings come from the environment (`COHERE_BASE_URL`, `COHERE_MODEL`) and rerank authorizes with `OPENROUTER_API_KEY`. If an eval exits 2 with `OPENROUTER_API_KEY` in the message, every case errored: rerank is unavailable. STOP, write `result.json` with a null candidate, report the cause in `summary`, and finish.

## When not to use

`recall` near zero or below 0.8 is a retrieval problem, not a ranking one. Use `chunking-tuning`, `retrieval-backend-selection` or `hybrid-fusion-tuning` before spending rerank calls on a weak candidate pool.

## Procedure

1. Widen the pool and keep a short list. Confirm `diffs` is non-empty. If the champion already has `rerank.enabled: true`, change only the pool size (for example `--set retrieval.top_k=100`) instead of re-enabling rerank.

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json
```

2. Compare on the full dev set (no `--limit`). Primary metric is `ndcg`. Save the output and print the gate summary as in the loop's step 5. Then apply the loop's gate and write `result.json`. This only recommends the candidate; the API decides.

```
deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

There is no `rerank.threshold` key; a `--set` of that name exits 1 with `error_type` `unknown_key`. A candidate with `errors > 0` (for example rerank timeouts) is not recommended: `candidate_config_path` stays null.

## Valid values

`rerank.enabled`: `true` or `false` (default `false`).

`rerank.top_n`: integer >= 1 (default 5). This is how many passages the reranker returns.

`retrieval.top_k`: 1-100 (default 10). This is the candidate pool before reranking. Set `top_k` above `top_n`, and keep `top_n` at or above `eval.cutoff_k` (default 5): a reranker that returns fewer documents than the cutoff caps `recall` and `precision`. Without rerank, a larger `top_k` alone does not change the scores (see `optimization-loop`).

## Index-time vs query-time

`rerank.enabled`, `rerank.top_n`, and `retrieval.top_k` are query-time. When rerank is on, `rerank.top_n` must be <= `retrieval.top_k` and `eval.cutoff_k` must be <= `rerank.top_n`. The index-time keys (`indexing.index_time_signature`) are `chunking.*` (`strategy`, `size`, `overlap`, `semantic_buffer_size`, `semantic_breakpoint_percentile`, `hierarchical_chunk_sizes`), `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width`. Also index-time: `EMBEDDING_MODEL`, `corpus_sha256`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, and `retrieval.graph_max_paths_per_chunk`. This skill changes none of them, so the candidate reuses the champion's index and the API does not rebuild one.

## Validated findings

Benchmark evidence, not product data.

Bundestag QA, 100-query retrieval-only eval on a 100-document index (7 gold documents for those questions plus 93 short distractors):

- Hybrid `top_k=10` with rerank off scored nDCG@5 0.743 and Recall@5 0.97.
- `top_k=50` plus `rerank.enabled=true` and `rerank.top_n=5` scored nDCG@5 0.948 and Recall@5 0.97 (`delta.ndcg` +0.204, `delta.recall` 0). Three rerank HTTP timeouts were counted as misses in that run and the nDCG gain was still above the 0.025 gate. Under the loop's current rule, a candidate with errors would not be recommended.

legal-rag-bench, 30-query retrieval-only eval:

- `top_k=50` dense retrieval plus `rerank.top_n=5` lifted nDCG@5 from 0.29 to 0.59 and Recall@5 from 0.50 to 0.80 versus un-reranked hybrid, and beat dense-only (0.53 / 0.67). Retrieval misses fell from 15 to 6 of 30. This is the largest measured lever.
- A naive 50/50 dense+BM25 reciprocal-rank hybrid can score below dense-only on legal corpora. Reranking repairs fusion rank noise, which is what makes hybrid safe.

## Pitfalls

- Rerank posts to OpenRouter: `POST <COHERE_BASE_URL>/rerank`, which with the default `https://openrouter.ai/api/v1` base is `https://openrouter.ai/api/v1/rerank`. The Authorization header is `Bearer <OPENROUTER_API_KEY>`. A base URL ending in `/v1` used to be a 404 because the Cohere SDK appended `/v2/rerank`; the DeepFix client never does, and no Cohere key is involved. Native Cohere (`https://api.cohere.com`) is not a supported base. A 404 or 401 here is an environment fault: stop and report it, do not edit the environment.
- Confirm hybrid is actually active. If eval stderr contains `falling back to dense`, the loaded index docstore was empty and BM25 was skipped.
- `top_k` above 100 fails validation (`mutate-config` exits 1).

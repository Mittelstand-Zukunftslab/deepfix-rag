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
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 15-step budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Re-ranking is query-time: do not re-index for `rerank.*` or `retrieval.top_k`, and do not pass `--index-dir` (the candidate reuses the champion's index).

The agent environment passes the Cohere settings (`COHERE_BASE_URL`, `COHERE_API_KEY`, `COHERE_MODEL`) through, so rerank works. If an eval exits 2 with `COHERE_API_KEY` in the message, every case errored: rerank is unavailable. STOP, write `result.json` with a null candidate, report the cause in `summary`, and finish.

## When not to use

`recall` near zero or below 0.8 is a retrieval problem, not a ranking one. Use `chunking-tuning`, `embedding-selection` or `query-rewriting` before spending rerank calls on a weak candidate pool.

## Procedure

1. Widen the pool and keep a short list. Confirm `diffs` is non-empty. If the champion already has `rerank.enabled: true`, change only the pool size (for example `--set retrieval.top_k=100`) instead of re-enabling rerank.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json
```

2. Compare on the full dev set (no `--limit`). Primary metric is `ndcg`. Save the output and print the gate summary as in the loop's step 5. Then apply the loop's gate and write `result.json`. This only recommends the candidate; the API decides.

```
deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

There is no `rerank.threshold` key; a `--set` of that name is silently ignored. A candidate with `errors > 0` (for example rerank timeouts) is not recommended: `candidate_config_path` stays null.

## Valid values

`rerank.enabled`: `true` or `false` (default `false`).

`rerank.top_n`: integer >= 1 (default 5). This is how many passages the reranker returns.

`retrieval.top_k`: 1-100 (default 10). This is the candidate pool before reranking. Set `top_k` above `top_n`.

## Index-time vs query-time

`rerank.enabled`, `rerank.top_n`, and `retrieval.top_k` are query-time.

## Validated findings

Benchmark evidence, not product data.

Bundestag QA, 100-query retrieval-only eval on a 100-document index (7 gold documents for those questions plus 93 short distractors):

- Hybrid `top_k=10` with rerank off scored nDCG@5 0.743 and Recall@5 0.97.
- `top_k=50` plus `rerank.enabled=true` and `rerank.top_n=5` scored nDCG@5 0.948 and Recall@5 0.97 (`delta.ndcg` +0.204, `delta.recall` 0). Three rerank HTTP timeouts were counted as misses in that run and the nDCG gain was still above the 0.025 gate. Under the loop's current rule, a candidate with errors would not be recommended.

legal-rag-bench, 30-query retrieval-only eval:

- `top_k=50` dense retrieval plus `rerank.top_n=5` lifted nDCG@5 from 0.29 to 0.59 and Recall@5 from 0.50 to 0.80 versus un-reranked hybrid, and beat dense-only (0.53 / 0.67). Retrieval misses fell from 15 to 6 of 30. This is the largest measured lever.
- A naive 50/50 dense+BM25 reciprocal-rank hybrid can score below dense-only on legal corpora. Reranking repairs fusion rank noise, which is what makes hybrid safe.

## Pitfalls

- Cohere rerank appends `/v2/rerank` to `COHERE_BASE_URL`. A base URL ending in `/v1` returns 404s that show up as per-query errors or as empty retrieval. This is an environment fault: stop and report it, do not edit the environment.
- Confirm hybrid is actually active. If eval stderr contains `falling back to dense`, the loaded index docstore was empty and BM25 was skipped.
- `top_k` above 100 fails validation (`mutate-config` exits 1).

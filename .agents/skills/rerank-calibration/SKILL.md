---
name: rerank-calibration
description: >
  Use when eval shows high recall but low ndcg: the relevant documents are retrieved
  and then ranked too low. Turn on rerank.enabled and raise retrieval.top_k.
  There is no rerank score-threshold field.
failure_mode:
  - retrieval_induced_error
trigger_conditions:
  - "recall is high while ndcg is low on eval --json"
  - "failure_distribution.retrieval_induced_error dominates and retrieved_doc_ids contain the gold id below the cutoff"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json"
  - "uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget. Re-ranking is query-time. Do not re-index for `rerank.*` or `retrieval.top_k`.

## When not to use

`recall` near zero is a retrieval miss. Use `chunking-tuning`, `query-rewriting`, or `embedding-selection` before spending a rerank call on an empty candidate pool.

## Procedure

1. Widen the pool and keep a short list for the generator. Confirm `diffs` is non-empty.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json
```

2. Compare. Primary metric is `ndcg`.

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

Accept only under the optimization-loop gate. There is no `rerank.threshold` key; a `--set` of that name is silently ignored.

## Valid values

`rerank.enabled`: `true` or `false` (default `false`).

`rerank.top_n`: integer >= 1 (default 5). This is how many passages reach generation.

`retrieval.top_k`: 1–100 (default 10). This is the candidate pool before reranking. Set `top_k` above `top_n`.

## Index-time vs query-time

`rerank.enabled`, `rerank.top_n`, and `retrieval.top_k` are query-time.

## Validated findings

Bundestag QA, 100-query retrieval-only eval on a 100-document index (7 gold documents for those questions plus 93 short distractors):

- Hybrid `top_k=10` with rerank off scored nDCG@5 0.743 and Recall@5 0.97.
- `top_k=50` plus `rerank.enabled=true` and `rerank.top_n=5` scored nDCG@5 0.948 and Recall@5 0.97 (`delta.ndcg` +0.204, `delta.recall` 0). Three rerank HTTP timeouts were counted as misses and still left the nDCG gain above the 0.025 gate.

legal-rag-bench, 30-query retrieval-only eval:

- `top_k=50` dense retrieval plus `rerank.top_n=5` lifted nDCG@5 from 0.29 to 0.59 and Recall@5 from 0.50 to 0.80 versus un-reranked hybrid, and beat dense-only (0.53 / 0.67). Retrieval misses fell from 15 to 6 of 30. This is the largest measured lever.
- A naive 50/50 dense+BM25 reciprocal-rank hybrid can score below dense-only on legal corpora. Compare `retrieval.backend=dense` and `hybrid` before trusting fusion. Reranking is what makes hybrid safe, because it repairs fusion rank noise.
- `deepfix hpo --retrieval-only` over query-time hybrid weights (`top_k`, `dense_weight`, `fusion_mode`, BM25 `k1`/`b`) plateaus near the dense baseline when no rerank stage is on.

## Pitfalls

- Cohere rerank appends `/v2/rerank` to `COHERE_BASE_URL`. A base URL ending in `/v1` returns 404s that show up as per-query `retrieval_miss` with empty retrieval. Strip the version suffix from the base URL.
- Confirm hybrid is actually active. If eval stderr contains `falling back to dense`, the loaded index docstore was empty and BM25 was skipped.
- `top_k` above 100 fails validation (`mutate-config` exits 1).

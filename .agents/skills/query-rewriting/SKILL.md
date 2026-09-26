---
name: query-rewriting
description: >
  Use when eval shows retrieval_miss because the query vocabulary does not match
  the corpus. Raise retrieval.query_expansion_num on hybrid or mixed retrieval.
  Dense and BM25 fusion weights apply only after fusion_mode is relative_score
  or dist_based_score.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "failure_distribution.retrieval_miss is the largest non-success bucket"
  - "recall is low while a manual dense lookup of the same fact succeeds"
  - "Queries use acronyms or paraphrases that never appear in the source text"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.query_expansion_num=3 --json"
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json"
  - "uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget. Hypothetical document embeddings (HyDE) are not implemented. Do not set a HyDE flag.

## When not to use

High `recall` and low `ndcg` means the documents were retrieved and then ranked poorly. Use `rerank-calibration`. Passages split mid-concept are `chunking-tuning`.

## Procedure

1. Expand the query. This is query-time on `hybrid` and `mixed` backends. Confirm `diffs` is non-empty.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.query_expansion_num=3 --json
```

2. Compare before touching fusion weights.

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

3. Only if expansion is flat, switch the fusion mode so weights are actually read, and set dense and BM25 weights together. Hybrid retrieval does not renormalize a lone `dense_weight`.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json
```

`retrieval.query_gen_prompt` is the expansion instruction. It is a multi-line string; edit it in the candidate YAML instead of `--set`. The template must keep `{num_queries}` and `{query}`.

## Valid values

`retrieval.query_expansion_num`: integer >= 0. HPO samples 1–5. `1` disables extra queries.

`retrieval.fusion_mode`: `reciprocal_rerank` (default), `relative_score`, `dist_based_score`, `simple`.

`retrieval.dense_weight` and `retrieval.bm25_weight`: 0.0–1.0. They are passed through only for `relative_score` and `dist_based_score`. The default `reciprocal_rerank` fusion does not use them.

`retrieval.backend`: `bm25`, `dense`, `hybrid`, `graph`, `mixed`. Expansion runs for `hybrid` and `mixed` only.

## Index-time vs query-time

`query_expansion_num`, `query_gen_prompt`, `fusion_mode`, and the fusion weights are query-time. Switching `retrieval.backend` to or from `graph` is index-time. If stderr contains `falling back to dense`, re-index before judging BM25 or hybrid.

## Validated findings

No checked-in A/B result for query expansion yet. On legal-rag-bench, a naive 50/50 dense+BM25 reciprocal-rank hybrid scored below dense-only until a reranker was added. See `rerank-calibration`.

## Pitfalls

- Setting `dense_weight=0.7` under `fusion_mode=reciprocal_rerank` does not change ranking.
- Setting `dense_weight` without `bm25_weight` leaves BM25 at 0.5, so the weights are not a 70/30 mix.
- `query_expansion_num` greater than 1 adds one LLM call per query on hybrid and mixed retrieval.
- A `diffs` object that omits the key you set means the mutation was ignored.

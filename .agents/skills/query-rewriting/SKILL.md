---
name: query-rewriting
description: >
  Use when eval shows retrieval_miss on a hybrid champion whose recall is already
  fairly high, because the query vocabulary does not match the corpus. Tune the
  hybrid fusion: set fusion_mode to relative_score or dist_based_score together
  with dense_weight and bm25_weight. The default reciprocal_rerank ignores the
  weights. Query expansion is not available to the agent.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "retrieval_miss > 0, recall >= 0.8 and the champion backend is hybrid (optimization-loop routing row 5)"
  - "Queries use acronyms or paraphrases that rarely appear in the source text, so dense and BM25 disagree"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 15-step budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Everything here is query-time: do not re-index and do not pass `--index-dir`.

Do not set `retrieval.query_expansion_num` above 1 in agent runs: it calls an LLM the agent does not have, so every case would error. Do not edit `retrieval.query_gen_prompt` either. Hypothetical document embeddings (HyDE) are not implemented.

## When not to use

High `recall` and low `ndcg` means the documents were retrieved and then ranked poorly. Use `rerank-calibration`. Passages split mid-concept are `chunking-tuning`. Low `recall` with no backend comparison yet is `embedding-selection`. This skill needs a `hybrid` champion: fusion weights do nothing on `dense` or `bm25`.

## Procedure

1. Switch the fusion mode so the weights are read, and set both weights together. Confirm `diffs` is non-empty and contains `retrieval.fusion_mode`.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json
```

Heuristic for the direction: queries with exact terms or acronyms favour more `bm25_weight` (for example 0.3 dense / 0.7 BM25); paraphrased queries favour more `dense_weight`. Choose one setting; do not sweep.

Alternative single hypothesis, BM25 saturation and length normalization:

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.bm25_k1=1.2 --set retrieval.bm25_b=0.5 --json
```

2. Compare on the full dev set (no `--limit`). Save the output and print the gate summary as in the loop's step 5. Then apply the loop's gate and write `result.json`. This only recommends the candidate; the API decides. Primary metric is `recall` when the goal is fewer misses.

```
deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

## Valid values

`retrieval.fusion_mode`: `reciprocal_rerank` (default), `relative_score`, `dist_based_score`, `simple`.

`retrieval.dense_weight` and `retrieval.bm25_weight`: 0.0-1.0 each. The fusion retriever always normalizes retriever weights to sum 1 (hybrid and mixed), so `0.7/0.3` and `0.35/0.15` are the same mix. A sum of 0 fails at retrieval time. The weights are used only by `relative_score` and `dist_based_score`; `reciprocal_rerank` (default) and `simple` ignore them.

`retrieval.bm25_k1`: 0.0-10.0 (default 1.5). `retrieval.bm25_b`: 0.0-1.0 (default 0.75). They apply to the BM25 side of `hybrid` and to `bm25`.

`retrieval.backend`: stay on `dense`, `hybrid` or `bm25`. Fusion applies to `hybrid` only.

## Index-time vs query-time

`fusion_mode`, the fusion weights, `bm25_k1` and `bm25_b` are query-time. If stderr contains `falling back to dense`, the BM25 docstore was empty: re-index into `<run_dir>/idx` and compare with `--index-dir <run_dir>/idx` before judging the result.

## Validated findings

No checked-in A/B result for fusion weights yet. Benchmark evidence, not product data: on legal-rag-bench, a naive 50/50 dense+BM25 reciprocal-rank hybrid scored below dense-only until a reranker was added. See `rerank-calibration`.

## Pitfalls

- Setting `dense_weight=0.7` under `fusion_mode=reciprocal_rerank` does not change ranking.
- Setting `dense_weight` without `bm25_weight` leaves BM25 at 0.5. After normalization that is 0.58 / 0.42, not 70 / 30.
- `query_expansion_num` above 1 adds an LLM call per query. The agent has no LLM, so cases error and the run must stop.
- A `diffs` object that omits the key you set means the mutation was ignored.

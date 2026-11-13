---
name: hybrid-fusion-tuning
description: >
  Use when eval shows retrieval_miss on a hybrid champion whose recall is already
  fairly high, because the query vocabulary does not match the corpus. Tune the
  hybrid fusion: set fusion_mode to relative_score or dist_based_score together
  with dense_weight and bm25_weight. The default reciprocal_rerank ignores the
  weights. Query expansion is not available to the agent. Formerly named
  query-rewriting. This skill does not rewrite queries.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "retrieval_miss > 0, recall >= 0.8 and the champion backend is hybrid (optimization-loop routing row 5)"
  - "Queries use acronyms or paraphrases that rarely appear in the source text, so dense and BM25 disagree"
cli_actions:
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json"
---

**In an API-driven round, query-time fusion and rerank go through `hpo-search`, not this skill.** `hpo-search` runs one bounded `deepfix hpo` sweep over the shipped small space (fusion mode, dense weight, `top_k`, rerank) and then confirms the best trial on the full dev set. This skill documents a single hand-picked fusion setting for manual use and for reading the findings; a round does not hand-write one candidate instead of the sweep.

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 40-turn budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Everything here is query-time: do not re-index and do not pass `--index-dir`.

Do not set `retrieval.query_expansion_num` above 1 in agent runs: it calls an LLM the agent does not have, so every case would error. The field is capped at 8 and the agent must leave it at 1. Do not edit `retrieval.query_gen_prompt` either. Hypothetical document embeddings (HyDE) are not implemented. This skill does not rewrite the query text.

## When not to use

High `recall` and low `ndcg` means the documents were retrieved and then ranked poorly. Use `rerank-calibration`. Passages split mid-concept are `chunking-tuning`. Low `recall` with no backend comparison yet is `retrieval-backend-selection` (formerly `embedding-selection`). This skill needs a `hybrid` champion: fusion weights do nothing on `dense` or `bm25`.

## Procedure

1. Switch the fusion mode so the weights are read, and set both weights together. Confirm `diffs` is non-empty and contains `retrieval.fusion_mode`.

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json
```

Before tuning weights, check that `retrieval.language` in `<run_dir>/input/champion.yaml` matches the corpus text (default `german`). It selects the stemmer on the BM25 side, so a mismatch weakens exactly the exact-term matching that the weights are meant to balance. If it does not match, make the language the one hypothesis of this run (`--set retrieval.language=english`, valid values listed in `retrieval-backend-selection`) and leave the fusion settings alone.

Heuristic for the direction: queries with exact terms or acronyms favour more `bm25_weight` (for example 0.3 dense / 0.7 BM25); paraphrased queries favour more `dense_weight`. Choose one setting; do not sweep.

Alternative single hypothesis, BM25 saturation and length normalization:

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.bm25_k1=1.2 --set retrieval.bm25_b=0.5 --json
```

2. Compare on the full dev set (no `--limit`). Save the output and run `deepfix gate` as in the loop. Then write `result.json`. This only recommends the candidate; the API decides. Primary metric is `recall` when the goal is fewer misses.

```
deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

If `effective_backend` is `dense` while the champion is `hybrid`, the BM25 docstore was empty. Re-index into `<run_dir>/idx` and compare with `--index-dir <run_dir>/idx` before judging the result. The gate rejects that fallback.

## Outside a round (manual use only)

The query-time sweep an API round runs is `hpo-search` with `configs/hpo_retrieval_small.yaml`. For a wider manual study, `configs/hpo.yaml` already covers `fusion_modes`, `dense_weight`, `bm25_k1` and `bm25_b`:

```
deepfix hpo --config <hpo_yaml> --paradigm hybrid --dataset <dataset_path> --retrieval-only --limit 30 --seed 0 --n-trials 20 --timeout 2400 --storage-uri sqlite:///hpo.db --output <out_yaml> --run-dir <run_dir> --json
```

`<hpo_yaml>` must set `base_config` to the champion and `query_expansion_num_min` and `query_expansion_num_max` to 1 under `search_space.hybrid`. Run the best config through the `optimization-loop` compare on the full dev set before trusting it. Do not run this inside a round: `hpo-search` is the round's one sweep.

## Valid values

`retrieval.fusion_mode`: `reciprocal_rerank` (default), `relative_score`, `dist_based_score`, `simple`.

`retrieval.dense_weight` and `retrieval.bm25_weight`: 0.0-1.0 each. The fusion retriever always normalizes retriever weights to sum 1 (hybrid and mixed), so `0.7/0.3` and `0.35/0.15` are the same mix. A sum of 0 fails at retrieval time. The weights are used only by `relative_score` and `dist_based_score`; `reciprocal_rerank` (default) and `simple` ignore them.

`retrieval.bm25_k1`: 0.0-10.0 (default 1.5). `retrieval.bm25_b`: 0.0-1.0 (default 0.75). They apply to the BM25 side of `hybrid` and to `bm25`.

`retrieval.backend`: stay on `dense`, `hybrid` or `bm25`. Fusion applies to `hybrid` only.

## Index-time vs query-time

`fusion_mode`, the fusion weights, `bm25_k1`, `bm25_b` and `language` are query-time. The index-time keys (`indexing.index_time_signature`) are `chunking.*` (`strategy`, `size`, `overlap`, `semantic_buffer_size`, `semantic_breakpoint_percentile`, `hierarchical_chunk_sizes`), `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width`. Also index-time: `EMBEDDING_MODEL`, `corpus_sha256`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, and `retrieval.graph_max_paths_per_chunk`. This skill changes none of them, so keep them equal to the champion and the candidate reuses its index.

## Validated findings

No checked-in A/B result for fusion weights yet. Benchmark evidence, not product data: on legal-rag-bench, a naive 50/50 dense+BM25 reciprocal-rank hybrid scored below dense-only until a reranker was added. See `rerank-calibration`.

## Pitfalls

- Setting `dense_weight=0.7` under `fusion_mode=reciprocal_rerank` does not change ranking.
- Setting `dense_weight` without `bm25_weight` leaves BM25 at 0.5. After normalization that is 0.58 / 0.42, not 70 / 30.
- `query_expansion_num` above 1 adds an LLM call per query. The agent has no LLM, so cases error and the run must stop.
- A `diffs` object that omits the key you set means the mutation was ignored or the key was rejected.

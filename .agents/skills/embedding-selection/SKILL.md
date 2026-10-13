---
name: embedding-selection
description: >
  Use when retrieval_miss persists and dense, bm25, and hybrid have not been
  compared on the same index. Compare retrieval.backend values among dense, bm25
  and hybrid, and on a bm25 or hybrid champion first check that retrieval.language
  matches the corpus. PCA flags do not change the retrieval index.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "failure_distribution.retrieval_miss > 0 and recall < 0.8 on eval --json (optimization-loop routing row 4)"
  - "dense, bm25, and hybrid have not been compared with eval --retrieval-only --compare"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=dense --json"
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=bm25 --json"
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.language=english --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 15-step budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt.

PCA (`data.enable_embedding_pca`, `data.embedding_pca_components`) only shapes the dataset built for `run-diagnostic`. It does not touch `index` or `eval`, so do not set it to improve retrieval.

## When not to use

High `recall` with low `ndcg` is `rerank-calibration`. Cut-off passages are `chunking-tuning`. A hybrid champion with high `recall` and a few misses is `query-rewriting` (fusion tuning).

## Procedure

One hypothesis per run: pick ONE backend that differs from the champion's (read `retrieval.backend` in `configs/champion.yaml`). A `hybrid` champion usually tries `dense`; a `dense` or `bm25` champion tries `hybrid`. Switching among `dense`, `bm25` and `hybrid` reuses the existing index when node text loads, so there is no re-index and no `--index-dir`. Confirm `diffs` is non-empty.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=dense --json
```

Compare on the full dev set (no `--limit`). Save the output and print the gate summary as in the loop's step 5, then apply the loop's gate and write `result.json`. This only recommends the backend; the API decides. Primary metric is `recall` when the goal is fewer misses.

```
deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

Other values for the same slot: `--set retrieval.backend=bm25`, `--set retrieval.backend=hybrid`.

Language check. When the champion is `bm25` or `hybrid`, read a few files in `corpus/` before choosing. If the text is not in the champion's `retrieval.language` (for example English text under the default `german`), the BM25 side stems the wrong language and misses exact terms. Then the one hypothesis for this run is the language, not the backend:

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.language=english --json
```

Compare it exactly as above. A `dense` champion is unaffected by this key. If hybrid wins on `ndcg` only after rerank, use `rerank-calibration` instead of fusion weights alone.

## Valid values

`retrieval.backend`: `bm25`, `dense`, `hybrid`, `graph`, `mixed`. This skill compares the first three. `graph` and `mixed` are rejected by the API and are not available in a round.

`retrieval.language` (default `german`) picks the Snowball stemmer on the BM25 side of `bm25` and `hybrid`. It does not touch `dense`. Valid values are the PyStemmer algorithms: `arabic`, `armenian`, `basque`, `catalan`, `danish`, `dutch`, `english`, `finnish`, `french`, `german`, `greek`, `hindi`, `hungarian`, `indonesian`, `irish`, `italian`, `lithuanian`, `nepali`, `norwegian`, `porter`, `portuguese`, `romanian`, `russian`, `serbian`, `spanish`, `swedish`, `tamil`, `turkish`, `yiddish`. Any other value fails when the BM25 retriever is built.

## Index-time vs query-time

The index-time keys (`indexing.index_time_signature`) are `chunking.*` (`strategy`, `size`, `overlap`, `semantic_buffer_size`, `semantic_breakpoint_percentile`, `hierarchical_chunk_sizes`), `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width`. The API builds its own index for a changed backend. `retrieval.language` is query-time: the stemmer is applied when the BM25 retriever is built, so a language candidate reuses the champion's index. In a round you may still measure a switch among `dense`, `bm25` and `hybrid` on the existing index. If stderr contains `falling back to dense`, the BM25 docstore was empty: re-index into `<run_dir>/idx` (`deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json`) and compare with `--index-dir <run_dir>/idx` before trusting a `bm25` or `hybrid` result.

## Validated findings

No checked-in A/B result isolates the backend on product data. Benchmark evidence, from `rerank-calibration`: on legal-rag-bench, dense-only beat a naive 50/50 hybrid until reranking was enabled.

## Pitfalls

- `data.enable_embedding_pca=true` does not compress the LanceDB index.
- An unknown key such as `embedding.model` is dropped and `diffs` stays empty.
- Scoring `bm25` or `hybrid` against an empty docstore silently runs dense retrieval, so the comparison is not real.

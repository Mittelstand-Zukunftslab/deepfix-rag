---
name: retrieval-backend-selection
description: >
  Use when retrieval_miss persists and dense, bm25, and hybrid have not been
  compared on a freshly built index. Compare retrieval.backend values among dense,
  bm25 and hybrid, and on a bm25 or hybrid champion first check that
  retrieval.language matches the corpus. The embedding model is env-only
  (EMBEDDING_MODEL) and is not a config key. PCA flags do not change the
  retrieval index. Formerly named embedding-selection.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "failure_distribution.retrieval_miss > 0 and recall < 0.8 on eval --json (optimization-loop routing row 4)"
  - "dense, bm25, and hybrid have not been compared with eval --retrieval-only --compare"
cli_actions:
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=dense --json"
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=bm25 --json"
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.language=english --json"
  - "deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --baseline-index-dir <run_dir>/idx --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 40-turn budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt.

This skill selects a retrieval backend. It does not select an embedding model. `EMBEDDING_MODEL` is an environment setting, not a `RAGConfig` field, and `mutate-config` rejects `embedding.model`.

PCA (`data.enable_embedding_pca`, `data.embedding_pca_components`) only shapes the dataset built for `run-diagnostic`. It does not touch `index` or `eval`, so do not set it to improve retrieval.

## When not to use

High `recall` with low `ndcg` is `rerank-calibration`. Cut-off passages are `chunking-tuning`. A hybrid champion with high `recall` and a few misses is `hybrid-fusion-tuning` (fusion tuning; formerly `query-rewriting`).

## Procedure

One hypothesis per run: pick ONE backend that differs from the champion's (read `retrieval.backend` in `<run_dir>/input/champion.yaml`). A `hybrid` champion usually tries `dense`; a `dense` or `bm25` champion tries `hybrid`. `retrieval.backend` is index-time. The API rebuilds its own index for that candidate, so the measurement has to use `<run_dir>/idx` or the local scores and the API re-score are not on the same index. Confirm `diffs` is non-empty and `index_time_changed` is true.

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=dense --json
```

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json
```

Compare on the full dev set (no `--limit`). Then run `deepfix gate` as in the loop and write `result.json`. This only recommends the backend; the API decides. Primary metric is `recall` when the goal is fewer misses.

```
deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --baseline-index-dir <run_dir>/idx --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

Other values for the same slot: `--set retrieval.backend=bm25`, `--set retrieval.backend=hybrid`.

Language check. When the champion is `bm25` or `hybrid`, read a few files in `corpus/` before choosing. If the text is not in the champion's `retrieval.language` (for example English text under the default `german`), the BM25 side stems the wrong language and misses exact terms. Then the one hypothesis for this run is the language, not the backend. Language is query-time, so do not re-index and do not pass `--index-dir`:

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.language=english --json
```

Compare it on the champion index. A `dense` champion is unaffected by this key. If hybrid wins on `ndcg` only after rerank, use `rerank-calibration` instead of fusion weights alone.

If the compare JSON has `effective_backend` `dense` while `configured_backend` is `bm25` or `hybrid`, the BM25 docstore was empty and the gate fails. Rebuild into `<run_dir>/idx` and compare with `--index-dir` before treating that as a backend result.

## Valid values

`retrieval.backend`: `bm25`, `dense`, `hybrid`, `graph`, `mixed`. This skill compares the first three. `graph` and `mixed` are rejected by the API and are not available in a round.

`retrieval.language` (default `german`) picks the Snowball stemmer on the BM25 side of `bm25` and `hybrid`. It does not touch `dense`. Valid values are the PyStemmer algorithms: `arabic`, `armenian`, `basque`, `catalan`, `danish`, `dutch`, `english`, `finnish`, `french`, `german`, `greek`, `hindi`, `hungarian`, `indonesian`, `irish`, `italian`, `lithuanian`, `nepali`, `norwegian`, `porter`, `portuguese`, `romanian`, `russian`, `serbian`, `spanish`, `swedish`, `tamil`, `turkish`, `yiddish`. Any other value fails when the config is validated.

## Index-time vs query-time

The index-time keys (`indexing.index_time_signature`) are `chunking.*` (`strategy`, `size`, `overlap`, `semantic_buffer_size`, `semantic_breakpoint_percentile`, `hierarchical_chunk_sizes`), `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width`. Also index-time: `EMBEDDING_MODEL`, `corpus_sha256`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, and `retrieval.graph_max_paths_per_chunk`. The API builds its own index for a changed backend, so measure that hypothesis on `<run_dir>/idx`. `retrieval.language` is query-time: the stemmer is applied when the BM25 retriever is built, so a language candidate reuses the champion's index.

## Validated findings

No checked-in A/B result isolates the backend on product data. Benchmark evidence, from `rerank-calibration`: on legal-rag-bench, dense-only beat a naive 50/50 hybrid until reranking was enabled.

## Pitfalls

- `data.enable_embedding_pca=true` does not compress the LanceDB index.
- An unknown key such as `embedding.model` exits 1 with `error_type` `unknown_key`.
- Scoring `bm25` or `hybrid` against an empty docstore reports `effective_backend` `dense`. The gate rejects that comparison.

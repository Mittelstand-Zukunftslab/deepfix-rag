---
name: chunking-tuning
description: >
  Use when eval shows retrieval_miss or retrieval_induced_error with low recall,
  or answers that stop mid-concept. Tune chunking.strategy, size, and overlap,
  then re-index into the run directory before scoring the candidate.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
trigger_conditions:
  - "failure_distribution.retrieval_miss / total >= 0.3 and recall < 0.6 on eval --json (optimization-loop routing row 2)"
  - "Retrieved passages are cut off mid-sentence or mid-statute"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set chunking.strategy=semantic --set chunking.semantic_breakpoint_percentile=90 --json"
  - "deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 15-step budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt. Chunking is index-time: a config edit without `index --force` scores the previous index.

## When not to use

High `recall` with low `ndcg` is a ranking problem. Use `rerank-calibration`. A moderate `recall` with misses that persist on coherent chunks is `embedding-selection` or `query-rewriting`.

## Procedure

1. Mutate one chunking hypothesis. Confirm `diffs` is non-empty.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set chunking.strategy=semantic --set chunking.semantic_breakpoint_percentile=90 --json
```

2. Rebuild into a directory under the run. Indexing reads the corpus from the config; do not pass `--dataset`.

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json
```

3. Compare on the full dev set (no `--limit`) as in the loop's step 5: save the output, print the gate summary. `--index-dir` applies to the candidate only. Then apply the loop's gate (`delta.ndcg` or `delta.recall` >= 0.025 for the primary metric, no regression in `ndcg`, `recall`, `precision`) and write `result.json`. This only recommends the candidate; the API decides.

```
deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

Use `recall` as the primary metric when the goal is fewer misses, `ndcg` when ranking quality is the goal. A hyperparameter search (`deepfix hpo`) is not available in a round; see the manual section below.

## Outside a round (manual use only)

`deepfix hpo` searches chunking and retrieval settings with Optuna. Never run it in an API-driven round: it takes far longer than the 15-step budget. The search space and the base config come from the HPO YAML, not from the champion, so copy `configs/hpo.yaml`, set `base_config` to the champion, and pass your own dataset:

```
deepfix hpo --config <hpo_yaml> --dataset <dataset_path> --retrieval-only --limit 30 --n-trials 10 --tune-chunking --output <out_yaml> --json
```

- Without `--tune-chunking` (or `tune_chunking: true` in the YAML) chunking is not searched.
- The default storage is `sqlite:///:memory:`, so the study is lost when the process ends; pass `--storage-uri sqlite:///hpo.db` to keep it.
- The dense and hybrid search spaces also sample `query_expansion_num` (1-5), which calls an LLM. Set `query_expansion_num_min` and `query_expansion_num_max` to 1 under `search_space.<paradigm>` when no LLM is configured.
- The run logs to the MLflow experiment named by `MLFLOW_DEV_RAG_EXPERIMENT`, which must already exist.
- `--output` writes the best config, not a recommendation. Take it through the `optimization-loop` compare on the full dev set before trusting it.

## Valid values

`chunking.strategy`: `sentence`, `recursive`, `fixed`, `semantic`, `hierarchical`.

`chunking.size`: 64-16000. `chunking.overlap`: >= 0. For `sentence` and `fixed`, overlap must be strictly less than size. `semantic`, `hierarchical`, and `recursive` skip that check.

Semantic splits also use `chunking.semantic_buffer_size` (>= 1) and `chunking.semantic_breakpoint_percentile` (0-100).

`hierarchical` derives nested sizes from `chunking.size` unless `chunking.hierarchical_chunk_sizes` is set. Do not pass that list through `--set`; it is a YAML list, and `--set` parses scalars only. Edit the candidate file, then re-index. Hierarchical retrieval wraps the retriever in an auto-merging retriever.

## Index-time vs query-time

The index-time keys (`indexing.index_time_signature`) are `chunking.*` (`strategy`, `size`, `overlap`, `semantic_buffer_size`, `semantic_breakpoint_percentile`, `hierarchical_chunk_sizes`), `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width`. Every `chunking.*` field is index-time, so each chunking candidate needs its own `deepfix index` into `<run_dir>/idx`. Keep `retrieval.backend`, `retrieval.vector_store` and `retrieval.bit_width` equal to the champion: if one differs, the API rebuilds the index for a reason other than the one you measured. Query-time knobs in this skill: none.

## Validated findings

No checked-in A/B result for chunking yet.

## Pitfalls

- Skipping `index --force` leaves `load_if_exists` on the old chunks, so eval cannot see the new strategy.
- Comparing without `--index-dir <run_dir>/idx` scores the champion's index for the candidate too.
- `overlap >= size` on `sentence` or `fixed` makes `mutate-config` exit 1.
- Semantic chunking embeds every boundary during indexing and is much slower than `sentence` or `fixed`; a slow index step can run past 30 s without output, so wait for it.

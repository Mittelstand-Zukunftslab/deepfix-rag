---
name: chunking-tuning
description: >
  Use when eval shows retrieval_miss or retrieval_induced_error with low recall,
  or answers that stop mid-concept. Tune chunking.strategy, size, and overlap,
  then re-index before scoring the candidate.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
trigger_conditions:
  - "failure_distribution.retrieval_miss / total >= 0.3"
  - "recall < 0.6 on eval --json"
  - "Retrieved passages are cut off mid-sentence or mid-statute"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set chunking.strategy=semantic --set chunking.semantic_breakpoint_percentile=90 --json"
  - "uv run --env-file .env deepfix index --config configs/candidate.yaml --index-dir indexes/candidate --force --json"
  - "uv run --env-file .env deepfix eval --config configs/candidate.yaml --index-dir indexes/candidate --compare configs/champion.yaml --retrieval-only --limit 30 --json"
  - "uv run --env-file .env deepfix hpo --tune-chunking --retrieval-only --limit 30 --n-trials 10 --output configs/best_chunking.yaml --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget. Chunking is index-time: a config edit without `index --force` scores the previous index.

## When not to use

High `recall` with low `ndcg` is a ranking problem. Use `rerank-calibration`. Vocabulary mismatch on an otherwise coherent corpus is `query-rewriting`.

## Procedure

1. Mutate one chunking hypothesis. Confirm `diffs` is non-empty.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set chunking.strategy=semantic --set chunking.semantic_breakpoint_percentile=90 --json
```

2. Rebuild into a new directory.

```
uv run --env-file .env deepfix index --config configs/candidate.yaml --index-dir indexes/candidate --force --json
```

3. Compare retrieval only, then apply the optimization-loop gate (`delta.ndcg` or `delta.recall` >= 0.025, no secondary regression).

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --index-dir indexes/candidate --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

Sweep only outside the 15-call iteration:

```
uv run --env-file .env deepfix hpo --tune-chunking --retrieval-only --limit 30 --n-trials 10 --output configs/best_chunking.yaml --json
```

## Valid values

`chunking.strategy`: `sentence`, `recursive`, `fixed`, `semantic`, `hierarchical`.

`chunking.size`: 64–16000. `chunking.overlap`: >= 0. For `sentence` and `fixed`, overlap must be strictly less than size. `semantic`, `hierarchical`, and `recursive` skip that check.

Semantic splits also use `chunking.semantic_buffer_size` (>= 1) and `chunking.semantic_breakpoint_percentile` (0–100).

`hierarchical` derives nested sizes from `chunking.size` unless `chunking.hierarchical_chunk_sizes` is set. Do not pass that list through `--set`; it is a YAML list, and `--set` parses scalars only. Edit the file, then re-index. Hierarchical retrieval wraps the retriever in an auto-merging retriever.

## Index-time vs query-time

Every `chunking.*` field is index-time. Query-time knobs in this skill: none.

## Validated findings

No checked-in A/B result for chunking yet.

## Pitfalls

- Skipping `index --force` leaves `load_if_exists` on the old chunks, so eval cannot see the new strategy.
- `overlap >= size` on `sentence` or `fixed` makes `mutate-config` exit 1.
- Semantic chunking embeds every boundary during indexing and is much slower than `sentence` or `fixed`.

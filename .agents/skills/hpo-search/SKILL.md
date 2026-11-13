---
name: hpo-search
description: >
  Query-time search procedure for an API-driven round under the 40 agent turns
  cap. Run ONE bounded `deepfix hpo` sweep over the shipped small retrieval space
  (rerank and/or fusion), then confirm the best trial on the FULL dev set with
  `deepfix eval` and `deepfix gate`. Do not hand-try candidates one at a time.
failure_mode:
  - retrieval_induced_error
  - retrieval_miss
trigger_conditions:
  - "optimization-loop routing row 3 (recall >= 0.8 and ndcg < recall - 0.15) or row 5 (retrieval_miss > 0 with recall >= 0.8 on a hybrid champion)"
  - "The lever is query-time (rerank.*, retrieval.top_k, retrieval.fusion_mode, fusion weights), so no re-index is needed"
cli_actions:
  - "deepfix hpo --config <run_dir>/input/hpo_retrieval_small.yaml --base-config <run_dir>/input/champion.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --run-dir <run_dir> --json"
  - "deepfix eval --config <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --seed 0 --index-dir <run_dir>/idx-cache --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --json"
  - "deepfix gate --compare <run_dir>/compare.json --primary ndcg --json"
---

Follow `optimization-loop` for the hard rules, the quality gate, the exit codes and the 40-turn budget. `<run_dir>` is the run directory named in the prompt (`runs/<run_id>/`); `<dev_path>` is the dev dataset path named in the prompt.

This skill replaces a single hand-written `mutate-config` hypothesis with **one** small Optuna sweep, followed by one full-dev confirmation. The sweep may score many configs internally; you still recommend at most one candidate.

## When not to use

- `errors > 0` on the baseline smoke: stop and write a blocked `result.json` (see the loop). Never sweep over a broken pipeline.
- Low `recall` because the passages are cut mid-concept: that is `chunking-tuning`, which is a separate one-hypothesis re-index path. Chunking is never part of this sweep.
- A backend comparison has not been made yet: `retrieval-backend-selection` is still the cheaper first hypothesis.
- `recall` near 1.0 with `ndcg` already above `recall - 0.15`: there is nothing rerank can fix here.

## The search space is fixed

`hpo_retrieval_small.yaml` ships the v1 space. `deepfix-sync download` puts it in the run bundle at `<run_dir>/input/hpo_retrieval_small.yaml`. Do not edit it:

- `rerank.enabled`: false, true. When it is on, `rerank.top_n`: 5 or 10, always <= that trial's `retrieval.top_k`.
- `retrieval.top_k`: 10, 20, 50.
- `retrieval.fusion_mode`: `reciprocal_rerank`, `relative_score`, `dist_based_score`. `simple` is excluded.
- `retrieval.dense_weight` (and the complementary `bm25_weight`): 0.3, 0.5, 0.7, **only** under `relative_score` or `dist_based_score`. The default `reciprocal_rerank` ignores them.
- Never sampled: `retrieval.bm25_k1`, `retrieval.bm25_b`, `retrieval.query_expansion_num` above 1, and every `chunking.*` key.

`12` trials on `20` seeded questions is the shipped budget. Do not raise `--n-trials` and do not point HPO at the full dev set.

## Procedure

### 1. Baseline smoke

Score the champion once, from the bundle's champion config. Keep its `recall` and `ndcg`; they are the numbers your confirmation compares against.

```
deepfix eval --config <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --seed 0 --index-dir <run_dir>/idx-cache --json
```

`--index-dir <run_dir>/idx-cache` is safe before the cache exists: the HPO call in step 3 fills it. If the champion index is already local, the flag is a no-op that keeps every eval on one path.

### 2. The index is localized for you

When the champion's `retrieval.lancedb_index_dir` is an `s3://` URI, `deepfix hpo` copies the whole prefix — bucket and key — **once** into `<run_dir>/idx-cache` before the first trial, and points every trial there. You never copy a prefix by hand and never pass `--index-dir` to `hpo`; the default is the cache under the run directory. Never write a cross-run cache.

Every later eval reads the same cache with `--index-dir <run_dir>/idx-cache`, so nothing in the round touches the object store twice.

### 3. One HPO call

Replace `<run_dir>` with the real path. `--base-config` points every trial at the champion snapshot in the bundle, so the search-space file does not have to name a workspace path.

```
deepfix hpo --config <run_dir>/input/hpo_retrieval_small.yaml --base-config <run_dir>/input/champion.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --run-dir <run_dir> --json
```

Notes:

- `--timeout 2400` is 40 minutes: headroom under the run limit and inside `terminal.timeout`. Stopping early is fine as long as one trial completed.
- The `deepfix` shim keeps `output/heartbeat.json` fresh for the whole call. A long silent HPO run is not a stall. Do **not** start a second copy.
- `--json` prints one payload at the end. Logs go to stderr.
- Artifacts appear as trials finish: `<run_dir>/hpo_trials.json` and, once there is a best trial, `<run_dir>/candidate.yaml`.
- The study lives in `<run_dir>/hpo.db`, so a re-run resumes instead of starting over.
- `deepfix hpo --help` is the authority on flags. Never invent one.

### 4. Write a provisional `result.json`

As soon as HPO reports a best trial, write `result.json` with the four required keys: `candidate_config_path` set to `<run_dir>/candidate.yaml`, `primary_metric` `ndcg`, a one-line `summary` naming the best trial's params, and `mlflow_run_id` null. A turn-cap wrap-up then still finds a valid file. Update it after the confirmation.

Subsample HPO scores are **not** a recommendation. Never put an HPO metric into `result.json` as the reason to keep a candidate.

### 5. Confirmation on the full dev set

No `--limit`. The candidate is query-time only, so no re-index. Both sides read the local cache: `--index-dir` covers the candidate and `--baseline-index-dir` covers the champion, so neither reaches S3. The baseline is the champion snapshot from the bundle, `<run_dir>/input/champion.yaml`, never `configs/champion.yaml`: the sandbox has no repository checkout, so that path does not exist here.

```
deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --json > <run_dir>/compare.json
```

Without `--baseline-index-dir`, `eval` scores the baseline on the index named in its own config, which is the `s3://` URI from the bundle. That is correct but re-reads the object store; the flag is what keeps the round on the local copy.

```
deepfix gate --compare <run_dir>/compare.json --primary ndcg --json
```

### 6. Apply the loop's gate

Recommend only when `deepfix gate` reports `pass` true under the existing thresholds (`OPTIMIZATION_MIN_PRIMARY_DELTA` default 0.025, `OPTIMIZATION_SECONDARY_TOLERANCE` default -0.01). A failing gate, `candidate_errors > 0`, or a hybrid-to-dense fallback means `candidate_config_path` is `null`. Do not run a second sweep and do not fall back to hand-trying candidates in the same round.

### 7. Finalize `result.json`

Fill in `mlflow_run_id` from the compare JSON's candidate side, restate baseline vs candidate and the gate decision in `summary`, `cat` the file, and finish. Do not promote.

## Reading the HPO JSON

- `best_trial.params` / `best_trial.values` come from the 20-question subsample. Use them only to describe the hypothesis.
- `stopped_early` is true when the sweep finished fewer trials than `--n-trials` — because `--timeout` stopped it or a trial failed. Say so in `summary`; it does not invalidate the confirmation.
- `failed_trials` counts trials Optuna recorded `FAIL`. Each one errored on every case, so it measured nothing and can never be the best trial. The error text is on the trial row's `user_attrs.error` in `hpo_trials.json`.
- `pruned_trials` counts duplicate parameter sets Optuna redrew. They are skipped without an eval — `user_attrs.duplicate_of` names the trial they repeat — so `n_trials` completed evaluations and `pruned_trials` is overhead, not lost budget.
- The exported `<run_dir>/candidate.yaml` holds the champion config plus the **tuned** parameters only: no `data.limit`, no `data.path_or_repo`, no `retrieval.lancedb_index_dir`. Do not expect the subsample or the cache path in it.
- `trials_path` points at `<run_dir>/hpo_trials.json`. `index_dir` / `index_cache_source` show the local index the trials used.
- `completed_trials` / `total_trials` describe the study, not your turn budget.

## Pitfalls

- Copying the S3 index prefix by hand. `deepfix hpo` localizes it, bucket and key intact, into `<run_dir>/idx-cache`. A manual copy burns turns and the cache is already where every eval should read from.
- Passing `--index-dir` to `hpo`. The default is the run cache; only pass it to choose a different directory under `<run_dir>`.
- Running HPO twice in one round. The budget is 40 turns and the gate is decided by the full-dev confirmation, not by a second sweep.
- Comparing the HPO subsample score with the baseline smoke (30 questions, different seed). Only the full-dev compare counts.
- Passing `--limit` to the confirmation. The gate needs every dev question.
- Editing `<run_dir>/input/hpo_retrieval_small.yaml`. The space is fixed for this release; a wider one needs a code change.
- Reaching for `configs/...` inside the container. The sandbox has no repository checkout: the search space and the champion both come from `input/` in the run bundle.
- Expecting `retrieval.dense_weight` to matter under `reciprocal_rerank`. The space does not sample it there for that reason.
- Treating an HPO trial error as a retrieval miss. Read `hpo_trials.json`: a failed trial state means the pipeline, not the ranking.
- Reading `best_trial: null` with `failed_trials` equal to the trial count as "no configuration helps". If every rerank trial failed with the same HTTP error, the rerank path is broken: stop, write a null candidate, and put that error in `summary`. Do not report rerank as a lever that does not work.

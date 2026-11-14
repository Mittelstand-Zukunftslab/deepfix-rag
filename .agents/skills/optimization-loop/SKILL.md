---
name: optimization-loop
description: >
  Entry skill for every DeepFix optimization round, read it first. Score the champion
  with deepfix eval, pick ONE pathology skill from the routing table, write one
  candidate under the run directory, compare it on the full dev set, and finish
  with a recommendation in result.json. For query-time work (rerank and fusion) the
  ONE hypothesis is a single bounded `deepfix hpo` sweep via `hpo-search`. The agent
  never promotes; the API decides.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
  - llm_reasoning_failure
  - deflection_failure
trigger_conditions:
  - "An optimization round is starting and no pathology skill has been chosen yet"
  - "eval --compare JSON is available and the candidate must be recommended or discarded"
cli_actions:
  - "deepfix eval --config <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --seed 0 --index-dir <run_dir>/idx-cache --json"
  - "deepfix hpo --config <run_dir>/input/hpo_retrieval_small.yaml --base-config <run_dir>/input/champion.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --run-dir <run_dir> --json"
  - "deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json"
  - "deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --json"
  - "deepfix gate --compare <run_dir>/compare.json --primary ndcg --json"
---

This skill is the whole contract for a round. Pathology skills are sibling folders of this one (`../chunking-tuning/SKILL.md`, and so on). Each names one hypothesis; this skill decides whether to recommend it.

## Prerequisite: the `deepfix` CLI

Every step below calls `deepfix`. Confirm it resolves before you plan anything:

```
command -v deepfix
```

A path means you can continue. No output means the CLI is not installed, so no round can run: install it once, then check again.

```
uv tool install "git+https://github.com/Mittelstand-Zukunftslab/deepfix-rag.git"
```

The package is not on PyPI, so the git source is the install. If `deepfix` is still missing afterwards, `uv tool update-shell` puts uv's tool directory on `PATH`; open a new shell and retry. In the API sandbox the shim is always present, so this check should only fail on a developer's own machine.

## Hard rules

- Write only under `<run_dir>` (the run directory named in the prompt, `runs/<run_id>/`). The champion is read-only input at `<run_dir>/input/champion.yaml`; never edit it, and never write anything under `indexes/current`.
- Backends: `dense`, `hybrid`, `bm25` only. The API rejects `graph` and `mixed`.
- Never promote. You recommend a candidate in `result.json`; the API re-scores it and decides.
- One hypothesis per run. If it fails the gate, write `result.json` with a null candidate and finish. Do not try a second one. A query-time hypothesis is one bounded `deepfix hpo` sweep (see `hpo-search`): the sweep may score many configs internally, but you still recommend at most one.
- Budget: 40 agent turns per round (`agent.max_turns` on this image). `skill_view` calls consume turns. Exhausting the budget is a wrap-up, not a cancel: still write `result.json`. On chat that ending is `finish_reason` `length` and `hermes.error_code` `iteration_budget_exhausted`. The API still requires `result.json`. No `run-diagnostic`, no diagnostic-server install or launch.
- You have no generation LLM and no query expansion. Keep `retrieval.query_expansion_num` at 1 (expansion calls an LLM). Changes to `generation.*` cannot be scored by the gate; do not make them.
- Never print or log API keys.

## Setup facts

- The sandbox has no repository checkout. Your run lives under `<run_dir>`: `input/` holds the bundle the API wrote (`input/champion.yaml`, `input/feedback_dev.jsonl`, `input/corpus`, `input/champion-index`, `input/hpo_retrieval_small.yaml`) and `output/` holds what you write. A `configs/champion.yaml` path does not exist here; always use `<run_dir>/input/champion.yaml`. Run plain `deepfix ...` from the workspace. Never `cd` elsewhere, never `uv run`, never pass `--env-file`.
- The `deepfix` command on PATH is the sandbox shim. While a command runs it rewrites `output/heartbeat.json` every 30 seconds and copies stdout and stderr to the container log. A direct call to the virtualenv interpreter does not write that file.
- `<dev_path>` is the dev dataset named in the prompt. Pass it as `--dataset <dev_path>` to every `eval`. Do not pass it to `index`: indexing reads the corpus from the config.
- Dev rows have only `query_id`, `query`, `doc_ids` and `negative_doc_ids`. There are no answers and no unanswerable rows. `ndcg` and `precision` are computed over judged documents only.
- Always use `--retrieval-only`. Metrics are `ndcg`, `recall`, `precision` at the cutoff `eval.cutoff_k` (default 5, set in the config). Only the first `eval.cutoff_k` retrieved documents are scored; `retrieval.top_k` does not set the cutoff.
- With `--json`, stdout is pure JSON and logs go to stderr.
- Commands that index or score can run longer than 30 s without output. If the terminal reports a command is still running, wait for it; do not start a second copy.

## Procedure

1. Baseline smoke test on 30 questions. Read `errors`, `total`, `recall`, `ndcg` and `failure_distribution` from the JSON.

```
deepfix eval --config <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --seed 0 --index-dir <run_dir>/idx-cache --json
```

If `errors > 0`, STOP: write `result.json` with a null candidate, put the first error from `error_details` in `summary`, and finish. Never read errored cases as retrieval misses. `eval` exits 2 when every case errored (missing embedding credentials, a missing `OPENROUTER_API_KEY` for rerank, an unreachable endpoint, or a wrong `--dataset`). If it exits before a summary, for example because the MLflow experiment does not exist, save that JSON as `<run_dir>/baseline_smoke.json` (or `<run_dir>/output/baseline_smoke.json` when `result.json` is written under `output/`). Set `state.json` `status` to `blocked` and copy the `error` string into `summary`. A null candidate with no champion metrics is a blocked baseline, not a recommendation.

2. Pick ONE pathology skill. Define `recall` and `ndcg` as the summary values, and `miss_rate = failure_distribution.retrieval_miss / total`. In `--retrieval-only` mode `retrieval_miss` means no judged relevant document was retrieved, and `retrieval_induced_error` means only some were. Take the first row that matches:

| # | Condition | Skill |
| --- | --- | --- |
| 1 | `errors > 0` | none: stop and report (step 1) |
| 2 | `miss_rate >= 0.3` and `recall < 0.6` | `chunking-tuning` |
| 3 | `recall >= 0.8` and `ndcg < recall - 0.15` | `hpo-search` |
| 4 | `retrieval_miss > 0` and `recall < 0.8` | `retrieval-backend-selection` |
| 5 | `retrieval_miss > 0`, `recall >= 0.8` and the champion backend is `hybrid` | `hpo-search` |
| 6 | anything else | none: write `result.json` with a null candidate and finish |

The thresholds come from a 30-question sample, where one question moves a metric by about 0.03. Pass `--seed 0` so that sample does not depend on file order. Read the chosen `SKILL.md`, then follow its procedure.

Rows 3 and 5 are query-time work: rerank and fusion. Follow `hpo-search` for both. It runs **one** `deepfix hpo` sweep over the shipped small space and then confirms the best trial on the full dev set — do not hand-write one `mutate-config` candidate instead. `chunking-tuning` and `retrieval-backend-selection` stay one-hypothesis, re-index paths with no HPO. `rerank-calibration` and `hybrid-fusion-tuning` remain readable for their single-setting recipes and their validated findings, but a round routes through `hpo-search`. `retrieval-backend-selection` was named `embedding-selection`, and `hybrid-fusion-tuning` was named `query-rewriting`; those old folders are aliases. The skills `deflection-and-refusal`, `legal-retrieval-grounding`, `multi-hop-aggregation` and `diagnostic-server` are not routed from here and must not be used in a round.

Before choosing, read `<workspace>/runs/ledger.jsonl` if it exists. Skip a hypothesis whose `skill` and `keys` already appear on a row with the same `champion_sha256` as `<run_dir>/input/champion.yaml`. After each command, write `<run_dir>/state.json` with `step`, `skill`, `commands`, and `status`.

3. Produce `<run_dir>/candidate.yaml`. **Query-time** (`hpo-search`, routing rows 3 and 5): one bounded sweep writes it.

```
deepfix hpo --config <run_dir>/input/hpo_retrieval_small.yaml --base-config <run_dir>/input/champion.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --run-dir <run_dir> --json
```

Write a provisional `result.json` as soon as that call reports a best trial, then continue to the confirmation. **Index-time** (`chunking-tuning`, `retrieval-backend-selection`): one `mutate-config`. An unknown `--set` key exits 1 with `error_type` `unknown_key` and writes nothing. On success, confirm `diffs` is non-empty. `ignored_keys` is always empty. `index_time_changed` tells you whether step 4 must rebuild.

```
deepfix mutate-config --base <run_dir>/input/champion.yaml --output <run_dir>/candidate.yaml --set rerank.enabled=true --set rerank.top_n=5 --set retrieval.top_k=50 --json
```

The example is a single-setting `rerank-calibration` hypothesis, kept for manual use. In a round, rows 3 and 5 go through `hpo-search` instead. Take the real command from the pathology skill you chose.

4. Re-index only when an index-time key changed (see the section below), into `<run_dir>/idx`. Use `<run_dir>/idx-<name>` if you need a second directory. Nothing in the `hpo-search` space is index-time, so that path skips this step.

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json
```

5. Compare on the FULL dev set. Do not pass `--limit`. The baseline is the bundle's champion, `<run_dir>/input/champion.yaml`. `--index-dir` applies to the candidate and `--baseline-index-dir` applies to the baseline, so passing both `<run_dir>/idx-cache` keeps the whole comparison on the local copy instead of re-reading S3. `deepfix hpo` fills that cache from the champion's remote index, bucket and key intact. Use `--index-dir <run_dir>/idx` instead only when you rebuilt an index in step 4. The output is long, so save it. `eval` exits 1 with `error_type` `stale_index` when the index manifest does not match the config, and exits 2 with `error_type` `partial_errors` when any compare case errored (`--max-error-rate` defaults to 0). Each side includes `effective_backend`, `configured_backend`, and `judge_failures`.

```
deepfix eval --config <run_dir>/candidate.yaml --compare <run_dir>/input/champion.yaml --dataset <dev_path> --retrieval-only --index-dir <run_dir>/idx-cache --baseline-index-dir <run_dir>/idx-cache --json > <run_dir>/compare.json
```

```
deepfix gate --compare <run_dir>/compare.json --primary ndcg --json
```

6. Apply the gate (next section). If it passes, the candidate is your recommendation. If it fails or `candidate_errors > 0`, the recommendation is null.

7. Write `<run_dir>/result.json` with exactly these keys, print it with `cat`, then call finish.

```
cat > <run_dir>/result.json <<'EOF'
{
  "candidate_config_path": "<run_dir>/candidate.yaml",
  "primary_metric": "ndcg",
  "summary": "One or two sentences: hypothesis, baseline vs candidate on the primary metric, gate result.",
  "mlflow_run_id": "<candidate.mlflow_run_id from the compare JSON>"
}
EOF
cat <run_dir>/result.json
```

- `candidate_config_path`: the path above when the gate passed; `null` (no quotes) when it failed, errored, or no hypothesis matched.
- `primary_metric`: exactly one of `ndcg`, `recall`, `precision` (lowercase, no `@k`), the metric your hypothesis targets. Use `ndcg` for a null candidate.
- `mlflow_run_id`: the candidate's `mlflow_run_id` from the compare JSON, or `null` if absent or not run.

## Quality gate

`deepfix gate` reads `OPTIMIZATION_MIN_PRIMARY_DELTA` (default 0.025) and `OPTIMIZATION_SECONDARY_TOLERANCE` (default -0.01). It uses the per-query `questions` in the compare JSON. Recommend the candidate only when the JSON field `pass` is true (`decision` is `keep`). That requires all of:

- `primary_delta` >= 0.025, with `primary` one of `ndcg`, `recall`, `precision`. The delta is the mean of paired per-query differences, and `ci95` must lie entirely above 0 (seeded 1000-sample bootstrap).
- None of `faithfulness`, `context_precision`, `context_recall`, `answer_relevancy`, `contextual_relevancy`, `ndcg`, `recall`, `precision` may fall past the secondary tolerance in `delta`. A drop of 0.005 is allowed. A drop of 0.02 is not. Retrieval-only runs score only `ndcg`, `recall` and `precision`.
- `errors` is 0 on both sides. `judge_failures` is 0. `effective_backend` equals `configured_backend` on both sides. A BM25 or hybrid run that fell back to dense does not pass.
- `errors`, `retrieval_induced_error_*` and `deflection_rate` are not gate metrics (lower is better). `retrieval_induced_error_reduction_pct` and its target flag do not replace the 0.025 rule.
- A missing delta is not an improvement.

The API then scores a holdout you cannot see. It rejects a candidate whose holdout primary delta is negative, whose holdout wins are not strictly greater than its losses, or that changes no holdout question.

A pass here is a recommendation, not a promotion. Do not recommend when `pass` is false. Retrieval-only metrics are deterministic; a delta under 0.025 is sampling noise, which the interval measures. The `--limit 30` smoke numbers must never be used for the gate. Add a ledger row by writing `state.json` before you finish so the next round can see this hypothesis.

## Index-time vs query-time

`retrieval.load_if_exists` defaults to true, so an existing index hides index-time edits. `deepfix index` writes `<index_dir>/deepfix_index.json`. `eval` exits 1 with `error_type` `stale_index` when that manifest does not match the config. `index --if-changed` returns `skipped: true` when the hash matches. The API treats these keys as index-time (`indexing.index_time_signature`, also listed by `deepfix schema --json`) and builds its own index when they differ from the champion: `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, `retrieval.bit_width`. Also index-time: `EMBEDDING_MODEL`, `corpus_sha256`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, and `retrieval.graph_max_paths_per_chunk`.

Query-time (no re-index; the existing index is reused): `retrieval.language`, `retrieval.top_k`, `retrieval.fusion_mode`, `retrieval.dense_weight`, `retrieval.bm25_weight`, `retrieval.bm25_k1`, `retrieval.bm25_b`, `rerank.enabled`, `rerank.top_n`. Everything the shipped `<run_dir>/input/hpo_retrieval_small.yaml` samples is in this list, so a sweep and its confirmation reuse the champion's index. Point both evals at `<run_dir>/idx-cache` so the reuse is local.

- `retrieval.top_k` only moves the scores through reranking (it is the pool the reranker sees) or, slightly, through hybrid fusion (each retriever returns `top_k` candidates before they are fused). With rerank off, a `top_k` above `eval.cutoff_k` does not change the dense or bm25 scores: on a 60-document test corpus the top 5 was identical for 10 of 10 queries on dense and on bm25, and changed for 2 of 10 on hybrid. A `top_k`-only candidate is not a useful hypothesis.
- Any `chunking.*` change needs `deepfix index` into `<run_dir>/idx` before the compare.
- A `retrieval.backend` switch is index-time. The API rebuilds for it, so measure on `<run_dir>/idx` (`deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json`, then `eval --index-dir <run_dir>/idx`). Do not reuse the champion index for a backend change: the local scores and the API re-score would not be the same index. If `effective_backend` is `dense` while the config says `bm25` or `hybrid`, the docstore was empty and the gate fails.
- Evaluating a candidate without `--index-dir` after `index --force` scores the index path stored in the YAML, which is the champion's.

## Exit codes

- `0`: success.
- `1`: validation error (bad flag, missing file, invalid value).
- `2`: runtime error (network, credentials, every case errored).

On `1` or `2`, fix the command or stop and report. Never retry an unchanged command, and do not read an exit 2 as a failed hypothesis.

## Validated findings

Benchmark evidence, not product data. Bundestag QA, first 100 training questions, retrieval-only. The index held those questions' 7 gold documents plus 93 short distractors, not the full corpus.

- Champion: hybrid, `top_k=10`, rerank off. nDCG@5 0.743, Recall@5 0.97, 3 `retrieval_miss`, 0 transport errors.
- One manual hypothesis (`rerank-calibration`, now part of the `hpo-search` space): `rerank.enabled=true`, `rerank.top_n=5`, `retrieval.top_k=50`, no re-index. Delta: nDCG@5 +0.204, Recall@5 +0.000. That would clear this gate.
- The same slice later spent a full 40-turn round hand-trying candidates without measuring rerank. A query-time round is now one `hpo-search` sweep plus one full-dev confirmation.

## Pitfalls

- A mutation that exits 1 with `error_type` `unknown_key` changed nothing. A success whose `diffs` object is empty also changed nothing.
- `--index-dir` on `eval` does not move the baseline; pass `--baseline-index-dir` when both sides should read the same local copy. Without it the baseline reads the index named in its own config, which for a bundle champion is the `s3://` URI.
- Copying an S3 index prefix by hand. `deepfix hpo` localizes it into `<run_dir>/idx-cache` with the bucket and key intact; a manual copy costs turns.
- Treating errored cases as retrieval misses sends you down the wrong pathology.
- `primary_metric` must be `ndcg`, `recall` or `precision`. Values such as `ndcg@5` or `faithfulness` are never scored, so the API rejects the run.

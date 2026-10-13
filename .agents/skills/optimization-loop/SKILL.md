---
name: optimization-loop
description: >
  Entry skill for every DeepFix optimization round, read it first. Score the champion
  with deepfix eval, pick ONE pathology skill from the routing table, write one
  candidate under the run directory, compare it on the full dev set, and finish
  with a recommendation in result.json. The agent never promotes; the API decides.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
  - llm_reasoning_failure
  - deflection_failure
trigger_conditions:
  - "An optimization round is starting and no pathology skill has been chosen yet"
  - "eval --compare JSON is available and the candidate must be recommended or discarded"
cli_actions:
  - "deepfix eval --config configs/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --json"
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.top_k=20 --json"
  - "deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json"
---

This skill is the whole contract for a round. Pathology skills are sibling folders of this one (`../chunking-tuning/SKILL.md`, and so on). Each names one hypothesis; this skill decides whether to recommend it.

## Hard rules

- Write only under `<run_dir>` (the run directory named in the prompt, `runs/<run_id>/`). Never edit `configs/champion.yaml` or anything under `indexes/current`.
- Backends: `dense`, `hybrid`, `bm25` only. The API rejects `graph` and `mixed`.
- Never promote. You recommend a candidate in `result.json`; the API re-scores it and decides.
- One hypothesis per run. If it fails the gate, write `result.json` with a null candidate and finish. Do not try a second one.
- Budget: 15 agent steps per round (about 10-12 are needed). No `deepfix hpo`, no `run-diagnostic`, no diagnostic-server install or launch.
- You have no generation LLM and no query expansion. Keep `retrieval.query_expansion_num` at 1 (expansion calls an LLM). Changes to `generation.*` cannot be scored by the gate; do not make them.
- Never print or log API keys.

## Setup facts

- The working directory is the product workspace: `configs/champion.yaml`, `corpus/`, `indexes/current`, `runs/<run_id>/`. Run plain `deepfix ...` from there. Never `cd` elsewhere, never `uv run`, never pass `--env-file`.
- `<dev_path>` is the dev dataset named in the prompt. Pass it as `--dataset <dev_path>` to every `eval`. Do not pass it to `index`: indexing reads the corpus from the config.
- Dev rows have only `query_id`, `query`, `doc_ids` and `negative_doc_ids`. There are no answers and no unanswerable rows. `ndcg` and `precision` are computed over judged documents only.
- Always use `--retrieval-only`. Metrics are `ndcg`, `recall`, `precision` at cutoff 5.
- With `--json`, stdout is pure JSON and logs go to stderr.
- Commands that index or score can run longer than 30 s without output. If the terminal reports a command is still running, wait for it; do not start a second copy.

## Procedure

1. Baseline smoke test on 30 questions. Read `errors`, `total`, `recall`, `ndcg` and `failure_distribution` from the JSON.

```
deepfix eval --config configs/champion.yaml --dataset <dev_path> --retrieval-only --limit 30 --json
```

If `errors > 0`, STOP: write `result.json` with a null candidate, put the first error from `error_details` in `summary`, and finish. Never read errored cases as retrieval misses. `eval` exits 2 when every case errored (missing embedding or Cohere credentials, unreachable endpoint, wrong `--dataset`).

2. Pick ONE pathology skill. Define `recall` and `ndcg` as the summary values, and `miss_rate = failure_distribution.retrieval_miss / total`. In `--retrieval-only` mode `retrieval_miss` means no judged relevant document was retrieved, and `retrieval_induced_error` means only some were. Take the first row that matches:

| # | Condition | Skill |
| --- | --- | --- |
| 1 | `errors > 0` | none: stop and report (step 1) |
| 2 | `miss_rate >= 0.3` and `recall < 0.6` | `chunking-tuning` |
| 3 | `recall >= 0.8` and `ndcg < recall - 0.15` | `rerank-calibration` |
| 4 | `retrieval_miss > 0` and `recall < 0.8` | `embedding-selection` |
| 5 | `retrieval_miss > 0`, `recall >= 0.8` and the champion backend is `hybrid` | `query-rewriting` |
| 6 | anything else | none: write `result.json` with a null candidate and finish |

The thresholds come from a 30-question sample, where one question moves a metric by about 0.03. Read the chosen `SKILL.md`, then follow its procedure. The skills `deflection-and-refusal`, `legal-retrieval-grounding`, `multi-hop-aggregation` and `diagnostic-server` are not routed from here and must not be used in a round.

3. Write the candidate to `<run_dir>/candidate.yaml` with the pathology skill's `mutate-config` command. Confirm the JSON `diffs` object is non-empty: unknown `--set` keys are silently ignored and the command still exits 0.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.top_k=20 --json
```

4. Re-index only when an index-time key changed (see the section below), into `<run_dir>/idx`. Use `<run_dir>/idx-<name>` if you need a second directory.

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --force --json
```

5. Compare on the FULL dev set. Do not pass `--limit`. `--index-dir` applies to the candidate only; the baseline is scored on the index named in `configs/champion.yaml`. Drop `--index-dir` when you did not re-index. The output is long, so save it and print the gate summary. Replace `ndcg` at the end with your primary metric.

```
deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx --compare configs/champion.yaml --dataset <dev_path> --retrieval-only --json > <run_dir>/compare.json
```

```
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));p=sys.argv[2];dl=d['delta'];hib=['faithfulness','context_precision','context_recall','answer_relevancy','contextual_relevancy','ndcg','recall','precision'];bad=[k for k in hib if dl.get(k,0)<0];e=d['candidate']['errors'];print(json.dumps({'primary':p,'primary_delta':dl.get(p),'regressed':bad,'candidate_errors':e,'baseline_errors':d['baseline']['errors'],'candidate_mlflow_run_id':d['candidate'].get('mlflow_run_id'),'delta':dl},indent=2));print('GATE','PASS' if dl.get(p,-1)>=0.025 and not bad and e==0 else 'FAIL')" <run_dir>/compare.json ndcg
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

`eval --compare` puts `candidate - baseline` in `delta`. The API applies this rule on the full dev set, then on a holdout you cannot see (holdout primary delta >= 0 and no more holdout losses than wins):

- `delta[primary] >= 0.025`, with `primary` one of `ndcg`, `recall`, `precision`.
- None of `faithfulness`, `context_precision`, `context_recall`, `answer_relevancy`, `contextual_relevancy`, `ndcg`, `recall`, `precision` may be negative in `delta`. Retrieval-only runs score only the last three, so in practice `ndcg`, `recall` and `precision` must all be `>= 0`.
- `errors`, `retrieval_induced_error_*` and `deflection_rate` are not gate metrics (lower is better). `retrieval_induced_error_reduction_pct` and its target flag do not replace the 0.025 rule.
- A missing delta is not an improvement.

A pass here is a recommendation, not a promotion. Do not recommend on `delta < 0.025`: that is judge noise, and the `--limit 30` smoke numbers must never be used for the gate.

## Index-time vs query-time

`retrieval.load_if_exists` defaults to true, so an existing index hides index-time edits. The API treats these keys as index-time (`promotion.index_time_signature`) and builds its own index when they differ from the champion: `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, `retrieval.bit_width`.

Query-time (no re-index; the existing index is reused): `retrieval.top_k`, `retrieval.fusion_mode`, `retrieval.dense_weight`, `retrieval.bm25_weight`, `retrieval.bm25_k1`, `retrieval.bm25_b`, `rerank.enabled`, `rerank.top_n`.

- Any `chunking.*` change needs `deepfix index` into `<run_dir>/idx` before the compare.
- To measure a `retrieval.backend` switch among `dense`, `bm25` and `hybrid` you may reuse the existing index (no `--index-dir`), as long as node text loads. If stderr says `falling back to dense`, the BM25 docstore was empty: re-index into `<run_dir>/idx` and compare again before trusting the result.
- Evaluating a candidate without `--index-dir` after `index --force` scores the index path stored in the YAML, which is the champion's.

## Exit codes

- `0`: success.
- `1`: validation error (bad flag, missing file, invalid value).
- `2`: runtime error (network, credentials, every case errored).

On `1` or `2`, fix the command or stop and report. Never retry an unchanged command, and do not read an exit 2 as a failed hypothesis.

## Validated findings

Benchmark evidence, not product data. Bundestag QA, first 100 training questions, retrieval-only. The index held those questions' 7 gold documents plus 93 short distractors, not the full corpus.

- Champion: hybrid, `top_k=10`, rerank off. nDCG@5 0.743, Recall@5 0.97, 3 `retrieval_miss`, 0 transport errors.
- One hypothesis (`rerank-calibration`): `rerank.enabled=true`, `rerank.top_n=5`, `retrieval.top_k=50`, no re-index. Delta: nDCG@5 +0.204, Recall@5 +0.000. That would clear this gate.

## Pitfalls

- A mutation with an empty `diffs` object changed nothing.
- `--index-dir` on `eval` does not move the baseline; do not expect the champion to be scored on the candidate index.
- Treating errored cases as retrieval misses sends you down the wrong pathology.
- `primary_metric` must be `ndcg`, `recall` or `precision`. Values such as `ndcg@5` or `faithfulness` are never scored, so the API rejects the run.

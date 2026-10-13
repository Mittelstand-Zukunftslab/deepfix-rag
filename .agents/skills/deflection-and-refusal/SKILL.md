---
name: deflection-and-refusal
description: >
  NOT FOR API-DRIVEN OPTIMIZATION RUNS (the promotion gate scores retrieval only and
  the agent has no generation LLM); manual and benchmark use only. For a developer
  who already has high recall and eval shows deflection_failure or llm_reasoning_failure:
  set generation.system_prompt so refusals contain a phrase the harness counts, such
  as "I do not have enough information".
failure_mode:
  - deflection_failure
  - llm_reasoning_failure
trigger_conditions:
  - "Manual or benchmark use only: recall is high and failure_distribution.deflection_failure > 0"
  - "Manual or benchmark use only: recall is high and failure_distribution.llm_reasoning_failure > 0"
  - "Manual or benchmark use only: deflection_rate is high on answerable questions, or near zero on unanswerable ones"
cli_actions:
  - 'deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set generation.system_prompt="If the context does not contain the answer, reply exactly: I do not have enough information." --set generation.response_synthesizer=compact_and_refine --json'
  - "deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dataset_path> --limit 30 --json"
---

NOT FOR API-DRIVEN OPTIMIZATION RUNS: the promotion gate scores retrieval only, so `generation.*` changes cannot pass it and the agent has no generation LLM. Manual/benchmark use only. The loop's routing table does not route here; never use this skill in a round.

Everything below is manual guidance for a developer with a generation LLM. `<run_dir>` is any scratch directory for the candidate; `<dataset_path>` is a benchmark or QA dataset path. Follow `optimization-loop` for exit codes and the shape of the gate, but note its primary metrics (`ndcg`, `recall`, `precision`) do not apply here. This skill is query-time. Do not re-index.

`deepfix dataset generate-unanswerable` is not implemented and always exits 2. Do not call it.

## When not to use

`retrieval_miss` and low `recall` mean the model never saw the evidence. Fix retrieval first. A refusal on a total miss is classified as `retrieval_miss`, not `deflection_failure`.

## Procedure

The harness marks a refusal only when the answer contains one of: `i do not have enough information`, `i don't have enough information`, `not enough information`, `i cannot answer`, `the context does not provide`, `the provided text does not`, `not mentioned in the context`, `i am unable to answer`, `does not contain the answer to this question`.

1. Set a system prompt that uses one of those phrases for unanswerable questions, and tell the model to answer when the context is sufficient. Quote the whole `system_prompt` value: it contains spaces and a colon, and an unquoted value makes the command fail with exit 2. Confirm `diffs` is non-empty. Default `generation.temperature` is already `0.0`; do not set it to `0.2`.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set generation.system_prompt="If the context does not contain the answer, reply exactly: I do not have enough information." --set generation.response_synthesizer=compact_and_refine --json
```

2. Compare with generation enabled. `--retrieval-only` skips the judge and cannot move `deflection_rate`. Manual primary metric is `faithfulness` or `answer_relevancy`, and `deflection_rate` must not rise on answerable rows.

```
deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dataset_path> --limit 30 --json
```

Benchmark datasets can contain unanswerable rows (empty ground-truth document ids). On those, a correct refusal is `success` and an answer is `deflection_failure`. Product dev data has no unanswerable rows. On any answerable row, a refusal with full recall is `deflection_failure`.

## Valid values

`generation.system_prompt`: string or null (default null).

`generation.response_synthesizer`: `compact_and_refine`, `simple_summarize` (default), `tree_summarize`, `refine`.

`generation.temperature`: 0.0-2.0, default 0.0. Leave it at 0.0 for refusal calibration.

`eval.threshold`: 0.0-1.0, default 0.7. The classifier uses it for faithfulness and answer relevancy. Raising it creates more `llm_reasoning_failure` labels; it does not change the model.

## Index-time vs query-time

`generation.*` and `eval.threshold` are query-time.

## Validated findings

No checked-in A/B result for refusal prompts yet.

## Pitfalls

- An unquoted `--set generation.system_prompt=If the context ...` is split by the shell and fails with exit 2.
- A custom refusal sentence that matches none of the phrases above is scored as a normal answer, so unanswerable benchmark rows stay `deflection_failure`.
- Setting `generation.temperature=0.2` raises the default, it does not lower it.
- `dataset generate-unanswerable` exits 2. Build unanswerable rows outside this CLI.
- In an API-driven run the gate cannot score `faithfulness` or `answer_relevancy` (the API scores retrieval only), so such a candidate is rejected.

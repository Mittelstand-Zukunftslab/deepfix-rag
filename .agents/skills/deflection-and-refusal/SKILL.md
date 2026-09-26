---
name: deflection-and-refusal
description: >
  Use when recall is already high and eval shows deflection_failure or
  llm_reasoning_failure. Set generation.system_prompt so refusals contain a
  phrase the harness counts, such as "I do not have enough information".
failure_mode:
  - deflection_failure
  - llm_reasoning_failure
trigger_conditions:
  - "recall is high and failure_distribution.deflection_failure > 0"
  - "recall is high and failure_distribution.llm_reasoning_failure > 0"
  - "deflection_rate is high on answerable questions, or near zero on unanswerable ones"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set generation.system_prompt=If the context does not contain the answer, reply exactly: I do not have enough information. --set generation.response_synthesizer=compact_and_refine --json"
  - "uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --limit 30 --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget. This skill is query-time. Do not re-index.

`deepfix dataset generate-unanswerable` is not implemented and always exits 2. Do not call it.

## When not to use

`retrieval_miss` and low `recall` mean the model never saw the evidence. Fix retrieval first. A refusal on a total miss is classified as `retrieval_miss`, not `deflection_failure`.

## Procedure

The harness marks a refusal only when the answer contains one of: `i do not have enough information`, `i don't have enough information`, `not enough information`, `i cannot answer`, `the context does not provide`, `the provided text does not`, `not mentioned in the context`, `i am unable to answer`, `does not contain the answer to this question`.

1. Set a system prompt that uses one of those phrases for unanswerable questions, and tell the model to answer when the context is sufficient. Confirm `diffs` is non-empty. Default `generation.temperature` is already `0.0`; do not set it to `0.2`.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set generation.system_prompt=If the context does not contain the answer, reply exactly: I do not have enough information. --set generation.response_synthesizer=compact_and_refine --json
```

2. Compare with generation enabled. `--retrieval-only` skips the judge and cannot move `deflection_rate`. Primary metric is `faithfulness` or `answer_relevancy`, and `deflection_rate` must not rise on answerable rows.

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --limit 30 --json
```

Unanswerable rows have empty ground-truth document ids. A correct refusal is `success`. An answer on an unanswerable row is `deflection_failure`. A refusal on an answerable row with full recall is also `deflection_failure`.

## Valid values

`generation.system_prompt`: string or null (default null).

`generation.response_synthesizer`: `compact_and_refine`, `simple_summarize` (default), `tree_summarize`, `refine`.

`generation.temperature`: 0.0–2.0, default 0.0. Leave it at 0.0 for refusal calibration.

`eval.threshold`: 0.0–1.0, default 0.7. The classifier uses it for faithfulness and answer relevancy. Raising it creates more `llm_reasoning_failure` labels; it does not change the model.

## Index-time vs query-time

`generation.*` and `eval.threshold` are query-time.

## Validated findings

No checked-in A/B result for refusal prompts yet.

## Pitfalls

- A custom refusal sentence that matches none of the phrases above is scored as a normal answer, so unanswerable rows stay `deflection_failure`.
- Setting `generation.temperature=0.2` raises the default, it does not lower it.
- `dataset generate-unanswerable` exits 2. Build unanswerable rows outside this CLI.

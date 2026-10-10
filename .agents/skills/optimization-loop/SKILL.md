---
name: optimization-loop
description: >
  Use at the start of every DeepFix optimization iteration, before a pathology skill.
  Run deepfix eval and run-diagnostic, mutate one hypothesis, re-index only when
  index-time keys change, then promote the candidate only if eval --compare shows
  the primary metric delta at least +0.025 with no secondary regression.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
  - llm_reasoning_failure
  - deflection_failure
trigger_conditions:
  - "An optimization iteration is starting and no pathology skill has been chosen yet"
  - "eval --compare JSON is available and the candidate must be promoted or discarded"
cli_actions:
  - "uv run deepfix eval --config configs/champion.yaml --retrieval-only --limit 30 --json"
  - "uv run deepfix run-diagnostic --config configs/champion.yaml --limit 30 --json"
  - "uv run deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.top_k=20 --json"
  - "uv run deepfix index --config configs/candidate.yaml --index-dir indexes/candidate --force --json"
  - "uv run deepfix eval --config configs/candidate.yaml --index-dir indexes/candidate --compare configs/champion.yaml --retrieval-only --limit 30 --json"
---

Run commands from the repo root as `uv run deepfix ...`. Do not pass `--env-file .env`. The repo `.env` points graph hosts at localhost and leaves passwords empty, and that flag fills any unset `FALKORDB_*` or `MEMGRAPH_*` from it. The console script is `deepfix`. Append `--json` so the payload is on stdout. Logs go to stderr.

This skill is the shared contract. Pathology skills in `.agents/skills/` name the hypothesis; this skill decides whether to keep it.

## When not to use

Do not start `deepfix hpo` inside an iteration. HPO is its own long run (`deepfix hpo --output configs/best_hpo.yaml`). One iteration is a single hypothesis, not a search.

## Procedure

Stay within 15 shell calls per iteration. Read `failure_mode` from `eval` rows and `failure_distribution` on the summary. `run-diagnostic` is optional context, not the source of those labels.

1. Score the champion. Prefer a cheap pass first.

```
uv run deepfix eval --config configs/champion.yaml --retrieval-only --limit 30 --json
```

2. Optionally diagnose. If this exits 2 because nothing is listening, follow `diagnostic-server` once, then retry. Skip diagnosis when a server is not available.

```
uv run deepfix run-diagnostic --config configs/champion.yaml --limit 30 --json
```

3. Pick one pathology skill from the routing table below and read its `SKILL.md`.
4. Write exactly one candidate. Confirm the JSON `diffs` object is non-empty. Unknown `--set` keys are ignored by `RAGConfig` and the command still exits 0.

```
uv run deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.top_k=20 --json
```

5. Re-index only when an index-time key changed (see below). Write a new directory so the champion index stays intact.

```
uv run deepfix index --config configs/candidate.yaml --index-dir indexes/candidate --force --json
```

6. Compare. Pass the candidate index with `--index-dir` when you re-indexed.

```
uv run deepfix eval --config configs/candidate.yaml --index-dir indexes/candidate --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

7. Apply the quality gate, then repeat a full eval without `--limit` only for a candidate that already passed the cheap pass.

## Quality gate

`eval --compare` puts absolute differences in `delta` (`candidate - baseline`) for metrics such as `ndcg`, `recall`, `precision`, `faithfulness`, and `answer_relevancy`.

Name the primary metric before the trial. Use `ndcg` for retrieval hypotheses and `faithfulness` or `answer_relevancy` for generation hypotheses.

Promote `configs/candidate.yaml` to the champion only when both are true:

- `delta.<primary> >= 0.025` (2.5 percentage points on a 0–1 metric).
- No secondary metric in `delta` is negative.

Otherwise keep the champion and discard the candidate. Do not treat a missing delta as an improvement.

`retrieval_induced_error_reduction_pct` is a separate percentage. The CLI flags it against a 15% error-reduction target. That flag does not replace the 0.025 gate.

## Routing

| Dominant signal | Skill |
| --- | --- |
| `retrieval_miss` high and `recall` low; answers look cut off | `chunking-tuning` |
| `retrieval_miss` with vocabulary mismatch on hybrid or dense | `query-rewriting` |
| `recall` already high and `ndcg` low | `rerank-calibration` |
| Dense vs BM25 vs hybrid has not been compared | `embedding-selection` |
| `deflection_failure` or `llm_reasoning_failure` while `recall` is high | `deflection-and-refusal` |
| Legal citations or statute structure missed on Legal RAG Bench | `legal-retrieval-grounding` |
| Partial recall across multiple documents | `multi-hop-aggregation` |

## Exit codes

- `0`: success.
- `1`: validation error. Fix the command or the config. Do not retry unchanged.
- `2`: runtime or network failure. Do not treat it as a failed hypothesis.

## Index-time vs query-time

`retrieval.load_if_exists` defaults to true, so an old index hides index-time edits. After any index-time change, run `index --force` into a new `--index-dir` and pass that directory to `eval`.

Index-time:

- `chunking.*`
- `retrieval.backend` when switching to or from `graph`
- `retrieval.graph_extraction_schema`
- `retrieval.graph_store`

Query-time (no re-index):

- `retrieval.top_k`, `retrieval.fusion_mode`, `retrieval.dense_weight`, `retrieval.bm25_weight`, `retrieval.graph_weight`
- `retrieval.bm25_k1`, `retrieval.bm25_b`, `retrieval.query_expansion_num`
- `rerank.enabled`, `rerank.top_n`
- `generation.*`
- `retrieval.graph_traversal_hops`

Switching among `dense`, `bm25`, and `hybrid` can reuse an index when node text is present. If eval stderr contains `falling back to dense`, BM25 found an empty docstore; re-index before trusting a hybrid or BM25 result.

## Diagnostics caveats

`run-diagnostic` calls the DeepFix analysis server at `DEEPFIX_SERVER_URL` (default `http://localhost:8844`). Install and launch that server with `diagnostic-server`. The client hardcodes `top_k=5` and uses PCA flags only to build that server's dataset. Per-case `failure_mode` values (`retrieval_miss`, `retrieval_induced_error`, `llm_reasoning_failure`, `deflection_failure`) come from `eval` rows.

## Validated findings

No checked-in end-to-end loop result yet. Measured retrieval levers live in `rerank-calibration`.

## Pitfalls

- `uv run --env-file .env` fills unset `FALKORDB_*` and `MEMGRAPH_*` from the repo `.env` (localhost, empty password). Omit the flag.
- A mutation with an empty `diffs` object changed nothing.
- Evaluating a candidate without `--index-dir` after `index --force` scores the index path stored in the YAML, which may still be the champion index.
- Promoting on `|delta| < 0.025` hill-climbs judge noise.

---
name: add-rag-mlflow-tracing
description: Add, repair, or update the Target RAG's central MLflow integration so it matches the project contract for tracing, candidate versioning, and evidence logging. Use for RAGs with absent, partial, duplicated, outdated, or incorrect MLflow instrumentation. Do not administer MLflow, change RAG behavior, instrument Hermes or DeepFix, or redesign evaluation.
---

# Add or Repair Target RAG MLflow Integration

Bring the Target RAG's own MLflow integration into conformance with the repository's central MLflow contract while preserving application behavior.

## Authoritative inputs

Before editing a Target RAG, read these repository files completely:

1. `MLflow logging conventions.txt` — authoritative evidence structure, field placement, types, trace hierarchy, candidate manifest shape, and evaluation aggregation contract.
2. `mlflow_helpers.py` — authoritative validation, canonicalization, and `config_hash` implementation.
3. `.env.example` — authoritative environment-variable and central resource names.

If this skill conflicts with one of those files, the contract file wins. Do not invent alternative workspace names, experiment names, service identities, candidate identifiers, metadata fields, metrics, or environment-variable names.

## Strict scope

Allowed:

- add, repair, consolidate, or remove Target RAG-side MLflow initialization;
- add/update the existing MLflow dependency through the repository's package manager;
- instrument the real RAG request path;
- repair incomplete or duplicated tracing;
- materialize and log the candidate manifest;
- create or safely reuse the configured candidate LoggedModel;
- associate traces and existing evaluation evidence with the candidate;
- update the Target RAG's own environment documentation with only the contract keys that runtime requires;
- run the smallest existing real request needed to verify the integration.

Out of scope:

- deploy, upgrade, configure, or administer central MLflow;
- create/delete workspaces, experiments, users, roles, or RBAC grants;
- start a local MLflow server;
- instrument Hermes, DeepFix, or unrelated services;
- create or redesign evaluation/scorers/benchmarks;
- alter RAG answers, retrieval, reranking, context construction, prompts, or other behavioral semantics;
- invent or auto-increment candidate IDs.

If a required central resource does not exist, fail clearly rather than creating a substitute.

## Required Target RAG state

The integration is complete only when all applicable requirements hold:

1. One canonical MLflow initialization path exists.
2. Required settings fail fast; there is no default workspace/experiment fallback.
3. The Target RAG uses only `MLFLOW_RAG_USERNAME` / `MLFLOW_RAG_PASSWORD`.
4. The current behavior is represented by a complete candidate manifest matching `mlflow_helpers.py` and the logging conventions.
5. `config_hash` is produced by the supplied `compute_config_hash()` implementation.
6. `MLFLOW_CANDIDATE_ID` maps to exactly one compatible LoggedModel in the configured RAG experiment.
7. The candidate's native MLflow `model_id` is active before request traces are emitted.
8. Every real RAG request has exactly one explicit top-level semantic span named `rag_request`.
9. Every applicable semantic stage appears beneath `rag_request` with the exact contract name:
   - `query_processing`
   - `query_rewriting` when enabled/executed
   - `retrieval`
   - `reranking` when present/executed
   - `context_assembly`
   - `generation`
10. Runtime evidence is attached to the applicable stage exactly as defined by `MLflow logging conventions.txt`.
11. Native MLflow trace/span duration is authoritative for latency; do not add duplicate custom latency fields.
12. Official framework/provider tracing may remain only when it does not duplicate calls or replace the required semantic contract hierarchy.
13. RAG behavior remains unchanged.

## Environment contract

Use only keys defined by `.env.example`.

### Resources

| Variable | Purpose |
| --- | --- |
| `MLFLOW_TRACKING_URI` | Central MLflow tracking server |
| `MLFLOW_DEV_WORKSPACE` | Normal development workspace |
| `MLFLOW_TEST_WORKSPACE` | Protected held-out workspace |
| `MLFLOW_DEV_RAG_EXPERIMENT` | Target RAG and visible-evaluation experiment |
| `MLFLOW_DEV_SMOKE_TEST_EXPERIMENT` | Development integration verification only |
| `MLFLOW_DEV_HERMES_EXPERIMENT` | Hermes tracing destination; never used by this skill |
| `MLFLOW_HIDDEN_EVAL_EXPERIMENT` | Protected held-out evaluation experiment |
| `MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT` | Protected held-out verification destination |

### Identities

| Variables | Purpose | Use by this skill |
| --- | --- | --- |
| `MLFLOW_RAG_USERNAME`, `MLFLOW_RAG_PASSWORD` | Write Target RAG and approved evaluation evidence | Required |
| `MLFLOW_DEEPFIX_USERNAME`, `MLFLOW_DEEPFIX_PASSWORD` | Read visible Target RAG evidence | Never |
| `MLFLOW_HERMES_READER_USERNAME`, `MLFLOW_HERMES_READER_PASSWORD` | Read visible Target RAG evidence | Never |
| `MLFLOW_HERMES_SERVICE_USERNAME`, `MLFLOW_HERMES_SERVICE_PASSWORD` | Write Hermes traces | Never |

### Evidence identifiers

| Variable | Purpose |
| --- | --- |
| `MLFLOW_CANDIDATE_ID` | Current immutable candidate identifier (`baseline_NNN` or `candidate_NNN`) |
| `MLFLOW_CORPUS_ID` | Canonical corpus identifier |

Never expose DeepFix or Hermes credentials to the Target RAG runtime.

MLflow Basic Auth clients expect `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD`. The Target RAG initializer may populate those process variables only from the RAG-specific credential pair before creating an MLflow client.

`MLFLOW_CANDIDATE_ID` is externally assigned. Never derive the next ID from MLflow, auto-increment it, rewrite `.env`, or substitute another ID after a conflict.

## Allowed destinations

| Purpose | Workspace | Experiment |
| --- | --- | --- |
| Normal RAG requests | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_RAG_EXPERIMENT` |
| Visible evaluation | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_RAG_EXPERIMENT` |
| Development smoke verification | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_SMOKE_TEST_EXPERIMENT` |
| Existing/in-scope held-out evaluation | `MLFLOW_TEST_WORKSPACE` | `MLFLOW_HIDDEN_EVAL_EXPERIMENT` |
| Held-out path smoke verification | `MLFLOW_TEST_WORKSPACE` | `MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT` |

Smoke experiments are verification destinations only. They must not create duplicate candidate LoggedModels.

## Implementation workflow

### 1. Audit the real RAG path

Identify:

- package manager and dependency state;
- application entry point and settings layer;
- orchestration framework/provider integrations;
- query preparation;
- optional query rewriting;
- retrieval/vector store;
- optional reranking;
- context assembly;
- generation/model call;
- effective behavioral configuration;
- smallest command/path that executes one real request.

Then locate every existing RAG-side MLflow integration point: initialization, tracking URI/workspace/experiment selection, credentials, active model, LoggedModel code, tracing decorators/context managers, provider autologging, custom spans, metadata, token/chunk/latency logging, Prompt Registry references, evaluation logging, implicit experiment creation, and hardcoded resources.

If remote access is available and an integration already runs, capture one representative pre-change trace before editing.

### 2. Reconcile semantic stages before editing

Build this working table from the actual runtime:

| Contract stage | Exists/enabled? | Actual runtime boundary | Current coverage | Required change | Verified? |
| --- | --- | --- | --- | --- | --- |
| `rag_request` | always | highest complete request-specific RAG boundary | ... | ... | ... |
| `query_processing` | determine | ... | ... | ... | ... |
| `query_rewriting` | determine | ... | ... | ... | ... |
| `retrieval` | determine | ... | ... | ... | ... |
| `reranking` | determine | ... | ... | ... | ... |
| `context_assembly` | determine | ... | ... | ... | ... |
| `generation` | determine | ... | ... | ... | ... |

Determine applicability from code/config/runtime, not current span names. If one function performs several stages, instrument the actual operations separately inside that function. Do not omit reranking or context assembly merely because they share helpers with neighboring stages.

### 3. Establish one fail-fast initializer

Reuse and repair an existing initializer where possible; otherwise consolidate competing paths.

For normal operation it must:

1. require tracking URI, RAG credentials, dev workspace, dev RAG experiment, candidate ID, and corpus ID;
2. map only RAG credentials to MLflow's standard Basic Auth variables;
3. set the remote tracking URI;
4. explicitly select the configured workspace;
5. look up the configured experiment by name;
6. fail if it does not already exist;
7. activate it by experiment ID to avoid silent creation caused by typos.

Remove hardcoded values, local-server startup, implicit experiment creation, duplicate initializers, and default workspace/experiment fallbacks.

### 4. Materialize the complete candidate manifest

Represent the current effective behavior exactly as required by `mlflow_helpers.py` and the logging conventions.

The manifest must contain:

- `schema_version`
- `candidate_id`
- `config_hash`
- `rag_type`
- `configuration`

`configuration` must explicitly include:

- `chunking`
- `embedding`
- `vector_store`
- `query_rewriting`
- `retrieval`
- `reranking`
- `context_assembly`
- `generation`
- `prompts`

Disabled optional behavior must still be represented explicitly. Prompt fields must contain exact immutable Prompt Registry version URIs, never mutable aliases. Use actual resolved runtime values; do not silently fill defaults or infer settings from class names.

Compute the hash by calling the supplied `compute_config_hash()`; do not reproduce or alter its algorithm.

### 5. Create or reuse the candidate LoggedModel safely

One candidate corresponds to one LoggedModel in `MLFLOW_DEV_RAG_EXPERIMENT`.

Required shape:

- LoggedModel name = exact `MLFLOW_CANDIDATE_ID`;
- native `model_id` generated by MLflow;
- immutable parameters `rag_type` and `config_hash`;
- artifact `candidate_manifest.json`.

Initialization must be restart-safe:

- no existing candidate name → initialize it, attach params + manifest, finalize ready;
- exactly one matching candidate with matching `config_hash` → reuse it;
- same candidate name with different `config_hash` → fail;
- never choose a replacement ID automatically;
- if creation fails after initialization, mark/finalize it failed rather than leaving ambiguous state.

Activate the resulting native model before normal request tracing using MLflow's active-model mechanism. Do not duplicate native `model_id` into custom metadata.

### 6. Reconcile automatic tracing

Prefer the highest-level official MLflow integration that matches the framework/provider actually used. Keep an existing correct integration; disable overlapping integrations that trace the same underlying call twice.

Automatic tracing is supporting evidence, not a substitute for the semantic contract hierarchy.

### 7. Enforce the contract trace hierarchy

Every real request must contain:

```text
rag_request
├── query_processing
├── query_rewriting       # when enabled/executed
├── retrieval
├── reranking             # when present/executed
├── context_assembly
└── generation
```

`rag_request` starts before request-specific RAG work and ends only after the final RAG output exists. Its native duration is the authoritative end-to-end RAG execution latency.

Framework/provider spans with names such as `retrieve` or `generate` may remain nested as lower-level evidence, but they do not replace `retrieval`, `generation`, or any other required semantic stage.

Instrument semantic operations, not every helper.

### 8. Log evidence on the correct trace/span

Within the active request trace, propagate immutable string metadata:

- `candidate_id` from `MLFLOW_CANDIDATE_ID`;
- `corpus_id` from `MLFLOW_CORPUS_ID`;
- `question_id` only for evaluation requests and only from the canonical evaluation input row.

Do not invent `question_id` for ordinary traffic and do not duplicate native `model_id`, `run_id`, or `trace_id` as custom metadata.

Follow `MLflow logging conventions.txt` exactly for evidence placement. At minimum, where applicable:

- retrieval: captured chunks when permitted, retrieval scores when available, `rag.retrieved_chunk_count`;
- reranking: captured reranked chunks/scores when available, `rag.reranked_chunk_count`;
- context assembly: actual assembled context when permitted, `rag.context_chunk_count`, derived `rag.context_tokens`;
- generation: captured input/output when supported, provider-native input/output/total token usage, derived system/question/context token counts.

Use the model-appropriate tokenizer for derived token counts. Capture actual runtime evidence rather than values copied from the candidate manifest.

Let exceptions occur inside instrumented spans so MLflow records native error status and structured exception information. Poor answer quality is not a technical error.

### 9. Preserve existing evaluation semantics

Do not create or redesign evaluation. If visible evaluation already exists, preserve its semantics while logging according to the contract:

- one run per `candidate_id × corpus_id × evaluator_version`;
- immutable run parameters for those identifiers;
- native candidate `model_id` association where supported;
- per-question scorer output as trace assessments;
- aggregate numeric quality/efficiency results on the evaluation run as specified by the conventions.

Do not invent scorers, evaluator versions, quality metrics, hidden-evaluation logic, or benchmark datasets.

### 10. Verify the integration

Run the smallest existing path that executes one real RAG request. Use the configured development smoke experiment for integration verification and restore the normal RAG experiment afterward.

Verify:

- imports/startup succeed;
- RAG output/behavior is unchanged;
- required env values fail fast;
- no resource/identity values are hardcoded;
- only RAG credentials are used;
- workspace + pre-existing experiment selection is correct;
- candidate creation/reuse behaves correctly and never changes the candidate ID;
- `candidate_manifest.json` matches the required schema;
- `config_hash` matches the supplied helper;
- LoggedModel has `rag_type` and `config_hash`;
- native candidate model association is present;
- trace metadata includes `candidate_id` and `corpus_id`;
- exactly one complete `rag_request` root exists;
- all applicable semantic stages appear beneath it with exact names;
- reranking/context assembly appear when actually used;
- required chunk/token/runtime evidence follows the conventions;
- overlapping tracing integrations do not duplicate provider/model calls;
- technical failures appear as native errors;
- no local MLflow server starts;
- no secrets enter tracked files.

When remote access is available, retrieve emitted traces and inspect the actual hierarchy and timeline. A trace merely existing is not sufficient verification. Compare the root wall-clock interval against child-stage placement; if unexplained time corresponds to a real semantic stage, instrument it. If it is framework/network/orchestration overhead outside the contract, report it rather than inventing a fake stage.

If remote credentials/resources are unavailable, finish static integration work and report exactly which remote checks remain unverified. Never weaken authentication or redirect traces to another destination as a workaround.

## Completion report

Report concisely:

1. starting integration state;
2. files/dependencies/configuration changed;
3. existing MLflow code kept/repaired/consolidated/removed;
4. candidate ID and resulting config hash;
5. automatic tracing integration kept/enabled and duplicates removed;
6. manual semantic spans added;
7. completed stage-reconciliation table;
8. required environment-variable names without secrets;
9. smoke command executed and behavior-preservation result;
10. candidate LoggedModel creation/reuse and native model association result;
11. `rag_request` hierarchy and remote trace-delivery result;
12. remaining unexplained root-timeline gap, if any;
13. unavailable contract evidence/resources, if any.

The task is complete only when the Target RAG integration conforms to the repository contract, or all RAG-side changes are complete and only unavailable external credentials/resources prevent remote verification.

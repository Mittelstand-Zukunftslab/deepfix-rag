---
name: add-rag-mlflow-tracing
description: Add, repair, or update the Target RAG's central MLflow integration so it matches the supplied project contract for tracing, candidate versioning, and evidence logging. Use for RAGs with no MLflow integration or with partial, duplicated, outdated, or incorrect RAG-side MLflow instrumentation. Do not deploy or administer MLflow, change RAG behavior, instrument other systems, or redesign evaluation.
---

# Add or Repair MLflow Integration for the Target RAG

Bring the **Target RAG's own MLflow integration** to the state defined by the supplied project contract.

The starting repository may have:

- no MLflow integration;
- a partially implemented integration;
- incomplete trace coverage;
- duplicate or overlapping tracing;
- incorrect MLflow initialization;
- incorrect candidate/version logging;
- stale or non-compliant RAG-side MLflow code.

Treat all of these as the same task: **make the RAG-side integration conform to the supplied contract with the smallest cohesive change set**.

Do not change answers, retrieval behavior, reranking behavior, prompt content, context construction, evaluation logic, or any other behavioral RAG semantics.

## Contract precedence

Before editing the Target RAG, read these supplied files completely:

- `helpers.py`: authoritative implementation for validating, canonicalizing, and hashing the complete behavioral RAG configuration.
- `MLflow logging conventions.txt`: authoritative MLflow structure, field placement, types, span evidence, candidate manifest shape, and evaluation aggregation contract.
- `.env.example`: authoritative environment-variable names and configured MLflow resource names.

If the skill text conflicts with one of those supplied contract files, **the supplied contract file wins**.

Do not extend the contract on your own:

- do not invent new workspace names;
- do not invent new experiment names;
- do not invent new service identities;
- do not invent new candidate identifiers;
- do not invent new metadata fields, metrics, or environment-variable names merely because they seem useful;
- use the dedicated smoke-test experiments only because they are explicitly defined by the supplied `.env.example`;
- do not invent any additional experiment or environment-variable names beyond that contract.

If a required contract file is unavailable, do not guess its contents. Report what is missing.

Reuse the supplied helper implementation rather than reimplementing its validation, canonicalization, or hashing logic. Adapt it into the Target RAG's normal module layout if necessary, but exclude illustrative top-level invocations that are not part of the reusable implementation.

## Strict scope

This skill concerns **only the Target RAG's MLflow integration**.

Allowed work:

- add, repair, consolidate, or remove RAG-side MLflow initialization code;
- add or update the RAG's MLflow dependency through its existing package manager;
- instrument the RAG request path;
- repair incomplete or incorrect RAG trace structure;
- remove duplicate RAG-side tracing integrations;
- materialize and log the RAG candidate manifest;
- create or reuse the RAG candidate LoggedModel in the already-configured experiment;
- associate Target RAG traces and existing evaluation evidence with that candidate;
- update the Target RAG's own `.env.example` documentation with contract-defined keys it actually requires;
- run the smallest existing RAG request needed to verify the integration.

Out of scope:

- deploying, upgrading, configuring, or administering the central MLflow server;
- starting a local MLflow server;
- creating, renaming, or deleting workspaces or experiments;
- changing authentication or RBAC;
- instrumenting DeepFix, Hermes, frontend, backend services unrelated to the RAG request path, or any other application;
- implementing DeepFix/Hermes workflows;
- creating a new evaluation framework, scorer suite, benchmark, or quality metric;
- changing RAG logic to make tracing easier;
- changing the candidate numbering convention;
- provisioning a smoke-test experiment or any other central MLflow resource.

Consume existing central resources. If a contract-required central resource does not exist, fail clearly and report it rather than creating a replacement.

## Non-negotiable RAG integration target

The task is not complete until the RAG-side integration satisfies all applicable items below.

1. There is one canonical RAG-side MLflow initialization path.
2. Required MLflow settings fail fast when missing; there is no silent default workspace or experiment.
3. Only the RAG service identity is usable by the Target RAG.
4. The current RAG behavior is represented by a complete candidate manifest matching the supplied helper and logging conventions.
5. The manifest's `config_hash` is computed with the supplied `compute_config_hash()` implementation.
6. The externally assigned `MLFLOW_CANDIDATE_ID` maps to one compatible MLflow LoggedModel in the configured RAG experiment.
7. The candidate's native `model_id` is active before RAG traces are emitted.
8. Every real RAG request has one explicit top-level contract span named `rag_request` that covers the complete request-specific RAG execution.
9. Every applicable semantic contract stage is represented beneath that root with the **exact contract stage name**:
   - `query_processing`
   - `query_rewriting` when enabled and executed
   - `retrieval`
   - `reranking` when present and executed
   - `context_assembly`
   - `generation`
10. Required runtime evidence is attached to the applicable spans exactly as defined by the logging conventions.
11. Native MLflow trace/span duration is used for latency; duplicate custom latency fields are not added.
12. Existing official framework/provider tracing may remain, but it must not cause duplicate calls or replace the required contract-level stage structure.
13. RAG behavior remains unchanged.

## Environment contract

Use only keys defined by the supplied `.env.example`.

The current project contract uses these shared resource keys:

| Variable | Purpose | Use by this skill |
| --- | --- | --- |
| `MLFLOW_TRACKING_URI` | Central tracking server | Required |
| `MLFLOW_DEV_WORKSPACE` | Normal development workspace | Required |
| `MLFLOW_TEST_WORKSPACE` | Protected held-out workspace | Only for an existing/in-scope held-out path |
| `MLFLOW_DEV_RAG_EXPERIMENT` | Target RAG and visible-evaluation experiment | Normal Target RAG destination |
| `MLFLOW_DEV_SMOKE_TEST_EXPERIMENT` | Development smoke-test experiment | RAG integration verification only |
| `MLFLOW_DEV_HERMES_EXPERIMENT` | Hermes tracing experiment | Never |
| `MLFLOW_HIDDEN_EVAL_EXPERIMENT` | Protected held-out evaluation experiment | Only for an existing/in-scope held-out path |
| `MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT` | Protected held-out smoke-test experiment | Only for verification of an existing/in-scope held-out path |

The central identities are deliberately separate:

| Identity variables | Intended access | Use in this skill |
| --- | --- | --- |
| `MLFLOW_RAG_USERNAME`, `MLFLOW_RAG_PASSWORD` | Write Target RAG evidence and protected evaluation evidence | Required |
| `MLFLOW_DEEPFIX_USERNAME`, `MLFLOW_DEEPFIX_PASSWORD` | Read visible Target RAG evidence | Never |
| `MLFLOW_HERMES_USERNAME`, `MLFLOW_HERMES_PASSWORD` | Read Target RAG evidence and write Hermes traces | Never |

Evidence identifiers:

| Variable | Purpose |
| --- | --- |
| `MLFLOW_CANDIDATE_ID` | Current immutable RAG candidate identifier |
| `MLFLOW_CORPUS_ID` | Canonical corpus identifier |


### Destination routing contract

The Target RAG integration may select only these destinations:

| RAG-side purpose | Workspace variable | Experiment variable |
| --- | --- | --- |
| Normal Target RAG requests | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_RAG_EXPERIMENT` |
| Visible evaluation requests | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_RAG_EXPERIMENT` |
| Development integration smoke verification | `MLFLOW_DEV_WORKSPACE` | `MLFLOW_DEV_SMOKE_TEST_EXPERIMENT` |
| Existing/in-scope held-out evaluation | `MLFLOW_TEST_WORKSPACE` | `MLFLOW_HIDDEN_EVAL_EXPERIMENT` |
| Smoke verification of that held-out path | `MLFLOW_TEST_WORKSPACE` | `MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT` |

The smoke experiments are **verification destinations only**. They do not define separate RAG candidates and must not cause duplicate candidate LoggedModels to be created.

Never route Target RAG traces to `MLFLOW_DEV_HERMES_EXPERIMENT`.

Do not rename these keys in application code and do not invent aliases such as `MLFLOW_RAG_EXPERIMENT`, `MLFLOW_RAG_SMOKE_EXPERIMENT`, `MLFLOW_HERMES_EXPERIMENT`, or `MLFLOW_HIDDEN_SMOKE_TEST`. Use the exact keys from the supplied `.env.example`.

A Target RAG deployment must receive only the RAG credential pair. Never copy real credentials into tracked files.

MLflow's basic-auth client reads `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD`. The Target RAG initializer must populate those process variables from `MLFLOW_RAG_USERNAME` and `MLFLOW_RAG_PASSWORD` before constructing an MLflow client.

`MLFLOW_CANDIDATE_ID` is externally assigned. Validate it against the project forms:

```text
baseline_NNN
candidate_NNN
```

Do not:

- auto-increment it;
- derive the next value from MLflow;
- rewrite `.env` at runtime;
- create a replacement candidate ID to work around an integration error.

## Implementation workflow

### 1. Audit the existing RAG and existing MLflow integration

Do not assume the repository is a clean greenfield setup.

Map the actual RAG request path:

- package manager;
- application entry point;
- settings/configuration layer;
- orchestration framework;
- model/provider clients;
- embedding client;
- retriever/vector store;
- query processing;
- optional query rewriting;
- retrieval;
- optional reranking;
- context assembly;
- generation;
- effective behavioral configuration;
- smallest existing command or path that executes one real request.

Then locate all existing RAG-side MLflow code:

- dependency declarations;
- MLflow initialization functions;
- tracking URI/workspace/experiment selection;
- authentication-variable mapping;
- active-model setup;
- LoggedModel/candidate code;
- tracing decorators and context managers;
- framework/provider autologging integrations;
- custom span wrappers;
- custom metadata;
- custom latency/token/chunk logging;
- prompt-registry references;
- evaluation-to-MLflow logging;
- code that creates experiments, workspaces, or local tracking servers;
- hardcoded MLflow resource names or credentials.

If a runnable MLflow integration already exists and remote access is available, run one representative request **before changing it** and inspect the resulting trace.

Do not decide that a RAG stage is absent merely because it is absent from MLflow. Determine stage existence from the code, constructed components, effective configuration, and runtime path.

### 2. Build the stage-reconciliation table before editing spans

Create this working table from the actual RAG:

| Contract stage | Exists / enabled? | Actual runtime boundary | Current MLflow coverage | Required change | Verified after change? |
| --- | --- | --- | --- | --- | --- |
| `rag_request` | always | highest complete RAG request boundary | ... | ... | ... |
| `query_processing` | determine | ... | ... | ... | ... |
| `query_rewriting` | determine | ... | ... | ... | ... |
| `retrieval` | determine | ... | ... | ... | ... |
| `reranking` | determine | ... | ... | ... | ... |
| `context_assembly` | determine | ... | ... | ... | ... |
| `generation` | determine | ... | ... | ... | ... |

Rules:

- Determine applicability from RAG code/config/runtime, not from current span names.
- If reranking exists in the RAG, `reranking` is mandatory even if the current trace only shows retrieval and generation.
- If context is assembled for the generator, `context_assembly` is mandatory even if prompt construction currently occurs inside another helper.
- If one function performs multiple contract stages, instrument the actual operations separately inside that function.
- If a stage is not applicable, record concrete evidence for that conclusion.
- Every applicable row must be verified after the change.

### 3. Establish a compatible MLflow dependency state

If MLflow is absent, declare a compatible MLflow 3 dependency through the repository's existing package manager and update the existing lockfile when applicable.

If MLflow is already present:

- inspect the pinned version and extras;
- keep them if compatible;
- update them only when required by the contract or framework/provider compatibility;
- do not add a duplicate MLflow dependency;
- do not introduce a second package manager.

### 4. Create, repair, or consolidate one fail-fast initializer

End with one canonical RAG-side initialization path.

Reuse an existing initializer if it can be made compliant. Otherwise replace or consolidate it. Remove competing RAG-side initialization paths that can select a different identity, workspace, or experiment.

The initializer must support only the contract-defined RAG destinations.

For normal Target RAG operation it must:

1. require the tracking URI, RAG username, RAG password, development workspace, development RAG experiment, candidate ID, and corpus ID;
2. map the RAG credential pair to MLflow's standard tracking credential variables;
3. configure the remote tracking URI;
4. select the required workspace;
5. look up the required experiment by its environment-provided name;
6. fail if that experiment does not already exist;
7. activate the experiment by ID so a typo cannot silently create a new experiment.

A suitable shape is:

```python
import os

import mlflow


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


_DESTINATIONS = {
    "rag": ("MLFLOW_DEV_WORKSPACE", "MLFLOW_DEV_RAG_EXPERIMENT"),
    "smoke": ("MLFLOW_DEV_WORKSPACE", "MLFLOW_DEV_SMOKE_TEST_EXPERIMENT"),
    "hidden_eval": ("MLFLOW_TEST_WORKSPACE", "MLFLOW_HIDDEN_EVAL_EXPERIMENT"),
    "hidden_smoke": (
        "MLFLOW_TEST_WORKSPACE",
        "MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT",
    ),
}


def configure_mlflow(*, destination: str = "rag"):
    os.environ["MLFLOW_TRACKING_USERNAME"] = require_env("MLFLOW_RAG_USERNAME")
    os.environ["MLFLOW_TRACKING_PASSWORD"] = require_env("MLFLOW_RAG_PASSWORD")
    mlflow.set_tracking_uri(require_env("MLFLOW_TRACKING_URI"))

    try:
        workspace_key, experiment_key = _DESTINATIONS[destination]
    except KeyError as exc:
        raise ValueError(f"Unsupported MLflow destination: {destination}") from exc

    mlflow.set_workspace(require_env(workspace_key))

    experiment_name = require_env(experiment_key)
    experiment = mlflow.MlflowClient().get_experiment_by_name(experiment_name)
    if experiment is None:
        raise RuntimeError(
            f"Configured MLflow experiment does not exist: {experiment_name}"
        )

    mlflow.set_experiment(experiment_id=experiment.experiment_id)
    return experiment
```

Adapt the shape to the repository's configuration conventions, but preserve the exact destination mapping and exact environment-variable names.

`hidden_eval` and `hidden_smoke` are relevant only if the Target RAG already has that held-out path or it is explicitly part of the requested RAG integration work. Do not build a held-out evaluator as part of this skill.

For remediation, explicitly remove or repair:

- hardcoded tracking/workspace/experiment values;
- fallback-to-default behavior;
- accidental local-server startup;
- duplicate initializers;
- use of DeepFix or Hermes credentials;
- implicit experiment creation.

### 5. Materialize the complete candidate manifest

Represent the current effective RAG behavior exactly as defined by the supplied conventions and `helpers.py`.

The manifest contains:

```text
schema_version
candidate_id
config_hash
rag_type
configuration
```

The complete `configuration` must contain all contract sections:

```text
chunking
embedding
vector_store
query_rewriting
retrieval
reranking
context_assembly
generation
prompts
```

Do not omit disabled optional behavior. Represent it explicitly.

The helper requires, among other fields:

```text
chunking:
  strategy
  chunk_size
  chunk_overlap
  parameters

embedding:
  provider
  model
  parameters

vector_store:
  type
  parameters

query_rewriting:
  enabled
  strategy
  provider
  model
  parameters

retrieval:
  strategy
  top_k
  parameters

reranking:
  enabled
  type
  model
  top_n
  parameters

context_assembly:
  strategy
  max_chunks
  parameters

generation:
  provider
  model
  parameters

prompts:
  system
  query_rewrite
```

Use actual effective runtime values. Do not infer values from class names or silently fill defaults that the runtime did not resolve.

When query rewriting is disabled, follow the helper's explicit disabled-state validation. Use exact immutable Prompt Registry version URIs in prompt fields, never mutable aliases. `prompts.query_rewrite` is `null` when it is not used.

Use `MLFLOW_CANDIDATE_ID` as `candidate_id`.

Obtain `rag_type` from an explicit application/configuration declaration. Do not guess it.

Call the supplied `compute_config_hash()` on the complete manifest/configuration. Do not reproduce or alter its hashing algorithm.

### 6. Create or reuse the candidate LoggedModel safely

One candidate is one MLflow LoggedModel in `MLFLOW_DEV_RAG_EXPERIMENT` in `MLFLOW_DEV_WORKSPACE`.

Do not create a second candidate LoggedModel in either smoke-test experiment. Smoke traces verify the same RAG integration/candidate; the smoke experiment is only a trace destination.

Required shape:

- LoggedModel name: exact `MLFLOW_CANDIDATE_ID`;
- native `model_id`: generated by MLflow;
- immutable LoggedModel parameter `rag_type`;
- immutable LoggedModel parameter `config_hash`;
- artifact `candidate_manifest.json`.

Initialization must be restart-safe:

- if no LoggedModel has the candidate name, initialize it, attach `rag_type`, `config_hash`, and the deterministic manifest artifact, then finalize it ready;
- if exactly one LoggedModel has that name and the `config_hash` matches, reuse it;
- if that candidate name exists with a different `config_hash`, fail clearly;
- do not choose another candidate ID automatically;
- if creation fails after initialization, finalize it failed rather than leaving ambiguous state.

After creation or reuse, call:

```python
mlflow.set_active_model(model_id=...)
```

before emitting normal RAG traces so MLflow creates the native model association.

Keep that same candidate active when sending a development smoke request. Do not create a smoke-specific candidate. If the installed MLflow version cannot preserve the native model association when a trace is routed to the smoke experiment, report that limitation; do not duplicate or move the LoggedModel to work around it.

Do not duplicate `model_id` as custom metadata.

Do not enable Git-based automatic model versioning. Candidate identity is the externally assigned candidate ID plus the complete behavioral configuration hash, not repository state.

### 7. Establish one non-overlapping automatic tracing layer

Prefer the official MLflow integration for the highest-level framework/provider actually used by the RAG.

If a suitable integration already exists:

- keep it when correct;
- repair its initialization/order if needed;
- disable redundant overlapping integrations if the same underlying model call is traced twice.

Do not enable multiple integrations that duplicate the same call.

Automatic tracing is useful lower-level evidence, but it does **not** by itself prove the contract trace is complete.

Inspect the actual trace before deciding which manual contract wrappers remain necessary.

### 8. Enforce the complete contract trace

Every real RAG request must have one explicit contract root:

```text
rag_request
├── query_processing
├── query_rewriting       # when enabled and executed
├── retrieval
├── reranking             # when present and executed
├── context_assembly
└── generation
```

#### Root boundary

`rag_request` must begin before request-specific RAG processing starts and end after the final RAG output for that request has been produced.

Do not accept as complete:

```text
retrieve
generate
```

or:

```text
retrieval
generation
```

with no complete RAG root.

The root's native duration is the authoritative end-to-end request latency.

#### Exact contract stage names

Use the contract names exactly.

If an automatic integration emits a related but differently named span such as:

```text
retrieve
generate
```

that automatic span may remain as a nested lower-level span, but it does not replace the required semantic wrappers:

```text
retrieval
generation
```

#### Semantic stage boundaries

Instrument the actual semantic operations:

- `query_processing`: request-specific query parsing, normalization, or preparation before retrieval;
- `query_rewriting`: rewriting/expansion operation when enabled;
- `retrieval`: actual retrieval/search/vector-store operation;
- `reranking`: post-retrieval re-scoring, rank fusion, cross-encoding, or any component that changes the retrieved ordering/selection;
- `context_assembly`: selection, truncation, deduplication, formatting, joining, or token-budget assembly that produces the context supplied to the generator;
- `generation`: answer-generation model boundary and directly associated output handling.

Do not collapse `reranking` into `retrieval` merely because they share a helper.

Do not collapse `context_assembly` into `generation` merely because prompt/context construction occurs immediately before the model call.

Prefer existing function boundaries. If one function crosses several contract stages, use `mlflow.start_span()` around the actual operations inside it.

Do not decorate every helper.

### 9. Log contract evidence on the correct trace/span

Within the active `rag_request` trace, attach immutable string metadata with `mlflow.update_current_trace(metadata=...)`:

- `candidate_id` from `MLFLOW_CANDIDATE_ID`;
- `corpus_id` from `MLFLOW_CORPUS_ID`;
- `question_id` from the canonical evaluation input row only when the request is an evaluation question.

Do not invent `question_id` for ordinary traffic.

Do not duplicate native `model_id`, `run_id`, or `trace_id` as custom metadata.

Follow `MLflow logging conventions.txt` for evidence placement.

At minimum, where applicable:

- `retrieval`
  - retrieved chunks when policy permits;
  - retrieval scores when available from RAG output;
  - `rag.retrieved_chunk_count = len(retrieval output)`;
- `reranking`
  - reranked chunks when policy permits;
  - reranking scores when available;
  - `rag.reranked_chunk_count = len(reranker output)`;
- `context_assembly`
  - actual assembled context when policy permits;
  - `rag.context_chunk_count`;
  - `rag.context_tokens` computed from the actual assembled context;
- `generation`
  - captured input/output when supported by the integration;
  - provider-native `input_tokens`, `output_tokens`, `total_tokens`;
  - `rag.system_tokens` from the resolved system prompt;
  - `rag.question_tokens` from the question actually sent to generation;
  - `rag.context_tokens` from the context actually sent to generation.

Use the model-appropriate tokenizer for derived token counts.

Capture actual runtime evidence, not values copied from the candidate manifest.

Use native span duration for stage latency and native root duration for total latency. Do not add duplicate flat latency fields.

Allow exceptions to occur inside instrumented spans so MLflow records native `ERROR` status and structured exception information. Do not label a merely low-quality answer as a technical error.

### 10. Reconcile existing bad RAG-side MLflow setups

Repair rather than layering another integration on top.

| Existing problem | Required RAG-side remediation |
| --- | --- |
| No MLflow integration | Add the contract integration. |
| Partial initializer | Repair/consolidate it to the fail-fast contract path. |
| Wrong experiment/workspace selection | Fix the RAG-side selection to use the supplied environment contract. |
| Implicit experiment creation | Replace it with lookup-by-name plus activation-by-ID. |
| Default workspace/experiment fallback | Remove the fallback. |
| Hardcoded resources or credentials | Replace them with supplied environment keys. |
| Local MLflow server startup | Remove it from the RAG integration. |
| Multiple RAG-side initializers | Consolidate to one canonical path. |
| Duplicate framework/provider traces | Disable the redundant integration/wrapper. |
| No `rag_request` root | Add the complete root around the RAG request path. |
| Only `retrieve` / `generate` spans | Keep useful automatic spans if desired, but add the required `retrieval` / `generation` contract wrappers and all other applicable stages. |
| Reranker exists but no reranking span | Instrument the actual reranker as `reranking`. |
| Context is assembled but no context span | Instrument it as `context_assembly`. |
| Stage spans exist outside the RAG root | Move/wrap instrumentation so they are descendants of `rag_request`. |
| Candidate exists with matching hash | Reuse it. |
| Candidate name exists with different hash | Fail and require an externally updated candidate ID. |
| Custom `model_id` metadata duplicates native association | Remove the duplicate custom field and use the native active-model association. |
| Custom latency duplicates native duration | Remove the duplicate latency field. |

Do not repair unrelated application code as part of this cleanup.

### 11. Preserve existing evaluation semantics only

This skill does not create or redesign evaluation.

If the Target RAG already performs visible evaluation, preserve its semantics while ensuring its MLflow logging follows the supplied conventions:

- one run per `candidate_id × corpus_id × evaluator_version`;
- immutable run parameters for those identifiers;
- native association to the candidate's `model_id` where supported;
- per-question scorer output remains trace assessments;
- numeric quality/efficiency results are aggregated on the evaluation run as defined by the conventions.

Do not invent:

- new scorers;
- evaluator versions;
- quality metrics;
- hidden-evaluation logic;
- benchmark datasets.

The protected held-out destination is used only by an existing or explicitly in-scope held-out evaluation path.

### 12. Verify the RAG integration

Smoke verification in this section exists only to verify the Target RAG's MLflow integration. It must not turn this skill into central MLflow administration, benchmark design, or instrumentation of another system.

Run the smallest existing command/path that exercises one real RAG request. Do not create a new test suite.

Development verification traces go to `MLFLOW_DEV_SMOKE_TEST_EXPERIMENT` in `MLFLOW_DEV_WORKSPACE`.

If verifying an already-existing/in-scope held-out RAG path, route only that verification request to `MLFLOW_HIDDEN_SMOKE_TEST_EXPERIMENT` in `MLFLOW_TEST_WORKSPACE`.

Do not create either experiment. They must already exist.

After smoke verification, restore the normal Target RAG destination (`MLFLOW_DEV_RAG_EXPERIMENT`) before normal RAG traffic resumes.

Verify:

- imports and application startup still succeed;
- RAG output/behavior is unchanged;
- required environment variables fail fast;
- no configured resource or identity value is hardcoded;
- only the RAG identity is used;
- the configured workspace and existing experiment are selected correctly;
- candidate initialization creates or reuses the correct LoggedModel without changing the candidate ID;
- `candidate_manifest.json` matches the required schema;
- its hash matches the supplied `compute_config_hash()`;
- the LoggedModel carries `rag_type` and `config_hash`;
- the active trace is natively associated with the candidate `model_id`;
- the trace contains `candidate_id` and `corpus_id`;
- exactly one complete `rag_request` root exists for the request;
- every applicable stage in the reconciliation table appears beneath that root with the exact contract name;
- reranking is present when the RAG actually reranks;
- context assembly is present when the RAG actually assembles context;
- required chunk/token/runtime evidence follows the logging conventions;
- no duplicate provider/model spans are created by overlapping integrations;
- native errors are recorded correctly;
- no local MLflow server starts;
- no secret enters tracked files.

When remote credentials and model access are available, retrieve the emitted trace from the configured workspace/experiment with `mlflow.search_traces()` and inspect the **actual hierarchy and timeline**.

Do not treat "a trace exists" as sufficient verification.

Check the root timeline for unexplained request time:

- do not sum child durations because spans may overlap;
- compare child-stage placement against the root wall-clock interval;
- if a material gap corresponds to a real RAG semantic stage, instrument that missing stage;
- if remaining time is genuine framework/network/orchestration overhead outside the contract stages, leave the RAG behavior unchanged and report the gap rather than inventing a fake stage.

If remote access is unavailable, complete static integration work and state exactly which remote checks remain unverified. Do not weaken authentication or redirect traces elsewhere.

## Completion report

Report concisely:

1. whether the starting RAG integration was absent, partial, duplicated, misconfigured, or already mostly compliant;
2. RAG-side MLflow files/dependencies/configuration changed;
3. existing MLflow code kept, repaired, consolidated, or removed;
4. candidate manifest source plus resulting candidate ID/config hash;
5. automatic tracing integration kept/enabled and duplicate integrations disabled;
6. manual contract spans added;
7. completed stage-reconciliation table, including evidence for not-applicable stages;
8. required environment-variable names, without printing secrets;
9. smoke/verification command run and whether RAG behavior remained unchanged;
10. whether LoggedModel creation/reuse and native model association were verified;
11. whether the complete `rag_request` hierarchy and remote trace delivery were verified;
12. any remaining unexplained root-timeline gap;
13. any contract evidence or external resource unavailable to the Target RAG.

The work is complete only when the **Target RAG's MLflow integration** conforms to the supplied contract, or when all RAG-side changes are complete and only unavailable external credentials/resources prevent remote verification.

Do not expand the task into central MLflow administration, evaluation design, or instrumentation of other systems.

## Official references

- [Tracing integrations](https://mlflow.org/docs/latest/genai/tracing/integrations/)
- [Tracing quickstart](https://mlflow.org/docs/latest/genai/tracing/quickstart/)
- [Trace search](https://mlflow.org/docs/latest/genai/tracing/search-traces/)
- [Token usage](https://mlflow.org/docs/latest/genai/tracing/token-usage-cost/)
- [Application version tracking](https://mlflow.org/docs/latest/genai/version-tracking/)
- [MLflow Python API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.html)
- [Workspaces](https://mlflow.org/docs/latest/self-hosting/workspaces/)
- [Authentication and RBAC](https://mlflow.org/docs/latest/self-hosting/security/)

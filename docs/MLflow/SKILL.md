---
name: add-mlflow-tracing
description: Add central MLflow tracing, candidate versioning, and evidence logging to an existing Python RAG baseline. Use when a RAG codebase must emit contract-compliant MLflow traces and candidate metadata without deploying MLflow or changing RAG behavior.
---

# Add MLflow Tracing

Integrate the existing RAG with the already-deployed central MLflow service. Make the smallest cohesive set of repository changes that produces a versioned candidate and useful, correlated traces without changing answers, retrieval behavior, or evaluation logic.

## Read the supplied contract first

Before editing the target repository, read these supplied files completely:

- `helpers.py`: authoritative implementation for validating, canonicalizing, and hashing the complete behavioral RAG configuration. Reuse its functions rather than recreating the hash algorithm.
- `MLflow logging conventions.txt`: authoritative field placement, types, span evidence, candidate manifest shape, and evaluation aggregation contract.
- `.env.example`: authoritative environment-variable names and configured MLflow resource names.

Locate the files if their paths differ. If either contract file is unavailable, do not invent a replacement contract; state what is missing. Adapt the supplied helper into the target repository's normal module layout, excluding any illustrative invocation that is not part of the helper implementation.

## Non-negotiable boundaries

- Instrument the Target RAG/application only. Do not deploy MLflow, start a local tracking server, or instrument DeepFix or Hermes.
- Consume existing workspaces, experiments, authentication, and RBAC. Do not create, rename, or grant access to them.
- Read every workspace name, experiment name, service identity, credential, and candidate ID from environment variables. Never embed their configured values in application code, scripts, fixtures, or generated configuration.
- Use only the RAG service identity in the Target RAG. The DeepFix and Hermes identities are separate principals and must never be used as fallbacks.
- Do not use literal fallback values for required MLflow settings. Missing settings must fail fast with a clear error.
- Never fall back to MLflow's default workspace or experiment.
- Preserve the existing package manager, application architecture, configuration loading, and runtime behavior.
- Prefer an official MLflow tracing integration for the framework/provider already in use. Add manual spans only for contract stages that remain invisible.
- Do not add new RAG quality metrics or redesign evaluation. Implement only logging for evidence the application already produces.
- Never log secrets, credentials, authorization headers, connection strings, environment dumps, or unrestricted configuration objects that may contain secrets.

## Environment contract

Use the variable keys in the supplied `.env.example`. Their values come from the deployed environment, never from source code.

Shared resource configuration:

| Variable | Purpose |
| --- | --- |
| `MLFLOW_TRACKING_URI` | Central tracking server |
| `MLFLOW_DEV_WORKSPACE` | Normal development workspace |
| `MLFLOW_TEST_WORKSPACE` | Protected held-out evaluation workspace |
| `MLFLOW_RAG_EXPERIMENT` | Target RAG and visible-evaluation experiment |
| `MLFLOW_HERMES_EXPERIMENT` | Hermes tracing experiment; not used by this skill |
| `MLFLOW_HIDDEN_EVAL_EXPERIMENT` | Protected held-out evaluation experiment |

The three central identities are deliberately separate:

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

The supplied central `.env.example` documents all three principals with non-secret placeholders. A Target RAG deployment must receive only the RAG credential pair; do not expose the DeepFix or Hermes credentials to it. When updating the Target RAG's own `.env.example`, add the shared destination/identifier keys and only the RAG credential keys needed by that runtime. Never copy real passwords into tracked files. Treat `.env.example` as documentation, not an automatic loader; reuse the application's existing settings mechanism. Add a dotenv dependency only if dotenv is already the project's convention.

MLflow's basic-auth client reads `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD`. The Target RAG initializer must populate those process variables from `MLFLOW_RAG_USERNAME` and `MLFLOW_RAG_PASSWORD` before constructing an MLflow client. This mapping keeps the configured identity in `.env` while ensuring the RAG cannot accidentally use either other principal.

`MLFLOW_CANDIDATE_ID` is externally assigned. Validate it against the project forms `baseline_NNN` and `candidate_NNN`. During baseline work, a human increments `baseline_NNN` in the deployed environment for every behaviorally distinct baseline. Do not auto-increment it, derive it from MLflow state, or rewrite `.env` at runtime. In the future Hermes will supply `candidate_NNN`; the RAG integration must accept that value unchanged.

## Implementation workflow

### 1. Map the existing RAG

Identify the package manager, entry point, settings layer, orchestration framework, model/provider clients, retriever/vector store, optional query rewriting and reranking, context assembly, generation boundary, effective behavioral configuration, and smallest command that executes one real request.

Do not assume a framework from filenames. Use the highest-level official MLflow autologging integration that matches the code actually executed. If the repository is not Python and there is no clearly supported integration, explain that a language-specific adaptation is needed instead of inventing an API.

### 2. Add the dependency through the existing package manager

Declare a compatible MLflow 3 dependency in the repository's dependency manifest and update its existing lockfile when applicable. Do not introduce a second package manager or rely only on an ad-hoc installation command when a manifest exists.

Check compatibility with the framework/provider versions already pinned before selecting an MLflow extra or integration.

### 3. Add one fail-fast MLflow initializer

Create or extend one repository-native initialization function. It must run before experiment lookup, candidate creation, autologging, or trace emission.

For the normal baseline path, it must:

1. require the tracking URI, RAG username, RAG password, development workspace, RAG experiment, candidate ID, and corpus ID environment variables;
2. map the RAG credential pair to MLflow's standard tracking credential variables for the current process;
3. configure the remote tracking URI;
4. call `mlflow.set_workspace()` with the development workspace value;
5. look up the RAG experiment by the environment-provided name and fail if it does not already exist;
6. activate the experiment by ID so a typo cannot silently create a new experiment.

The protected held-out evaluation path, only when it already exists or is explicitly in scope, follows the same sequence with the test workspace and hidden-evaluation experiment variables. It uses the same RAG identity. Never let a normal request select the protected destination.

A suitable shape is:

```python
import os

import mlflow


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def configure_mlflow(*, held_out: bool = False):
    os.environ["MLFLOW_TRACKING_USERNAME"] = require_env("MLFLOW_RAG_USERNAME")
    os.environ["MLFLOW_TRACKING_PASSWORD"] = require_env("MLFLOW_RAG_PASSWORD")
    mlflow.set_tracking_uri(require_env("MLFLOW_TRACKING_URI"))

    workspace_key = "MLFLOW_TEST_WORKSPACE" if held_out else "MLFLOW_DEV_WORKSPACE"
    experiment_key = (
        "MLFLOW_HIDDEN_EVAL_EXPERIMENT" if held_out else "MLFLOW_RAG_EXPERIMENT"
    )
    mlflow.set_workspace(require_env(workspace_key))

    experiment_name = require_env(experiment_key)
    experiment = mlflow.MlflowClient().get_experiment_by_name(experiment_name)
    if experiment is None:
        raise RuntimeError(f"Configured MLflow experiment does not exist: {experiment_name}")

    mlflow.set_experiment(experiment_id=experiment.experiment_id)
    return experiment
```

Adapt function names and structure to the repository, but preserve the no-fallback, identity isolation, and no-implicit-creation behavior. Do not put any configured workspace, experiment, or service identity value in this function.

### 4. Materialize the complete candidate manifest

Represent the current effective RAG behavior as the manifest defined by the supplied logging conventions. Include all required configuration sections, every behavior-affecting value, and explicit disabled states. Read values from the application's real settings and constructed components; do not infer a value from a class name or silently fill a default that the runtime did not resolve.

The manifest contains:

```text
schema_version
candidate_id
config_hash
rag_type
configuration
```

The complete `configuration` covers:

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

Use `MLFLOW_CANDIDATE_ID` for `candidate_id`. Use exact immutable Prompt Registry version URIs in the prompt fields, never a mutable alias. Obtain `rag_type` from an explicit application/configuration declaration; do not guess it.

Call the supplied `compute_config_hash()` after constructing the complete effective configuration, then place the returned hash in the manifest. The helper intentionally excludes identity/execution metadata from the behavioral hash. Do not change that boundary.

### 5. Create or reuse the candidate LoggedModel safely

One candidate is one MLflow LoggedModel in the environment-selected RAG experiment:

- LoggedModel name: the exact `MLFLOW_CANDIDATE_ID` value;
- immutable LoggedModel parameters: `rag_type` and `config_hash`;
- artifact: `candidate_manifest.json` serialized deterministically;
- native `model_id`: generated by MLflow, never duplicated as a custom identifier.

Make initialization restart-safe:

- if no LoggedModel has the candidate name, initialize it, attach the two parameters and manifest artifact, and finalize it as ready;
- if exactly one LoggedModel has that name and its `config_hash` matches, reuse it;
- if that name exists with a different hash, fail clearly because the candidate ID is immutable and must be manually incremented externally;
- do not choose the next candidate ID or mutate the environment;
- if candidate creation fails after initialization, finalize it as failed rather than leaving ambiguous state.

Use the MLflow 3 LoggedModel APIs supported by the installed version, such as `initialize_logged_model`, `MlflowClient.log_model_artifact`, and `finalize_logged_model`. After creation or reuse, call `mlflow.set_active_model(model_id=...)` before any request so traces receive the native model association automatically.

Do not enable Git-based automatic model versioning: this project's candidate identity is the full behavioral configuration represented by `MLFLOW_CANDIDATE_ID` and `config_hash`, not repository state.

### 6. Enable supported automatic tracing

After selecting the workspace/experiment and active model, enable the official integration for the highest-level framework actually used. Examples include the MLflow integrations for LlamaIndex, LangChain, and provider SDKs.

Inspect one resulting trace before adding custom spans. Do not enable overlapping integrations that duplicate the same model call, and do not replace the application's framework to gain an integration.

### 7. Complete the required trace structure

Ensure a top-level RAG request trace exists and add only the missing applicable stage spans:

```text
rag_request
├── query_processing
├── query_rewriting       when enabled
├── retrieval
├── reranking             when present
├── context_assembly
└── generation
```

Prefer existing function boundaries. Use `@mlflow.trace` or `mlflow.start_span()` with an appropriate span type. Do not decorate every helper.

Within the active root trace, attach immutable string metadata with `mlflow.update_current_trace(metadata=...)`:

- `candidate_id` from `MLFLOW_CANDIDATE_ID`;
- `corpus_id` from `MLFLOW_CORPUS_ID`;
- `question_id` from the canonical evaluation input row when the request is an evaluation question.

Do not invent `question_id` for ordinary traffic. Do not duplicate native `model_id`, `run_id`, or `trace_id` as custom metadata.

Follow the supplied logging conventions for inputs, outputs, attributes, and types. In particular:

- retain retrieved and reranked content/scores when policy permits;
- record `rag.retrieved_chunk_count`, `rag.reranked_chunk_count`, and `rag.context_chunk_count` on their applicable spans;
- record `rag.context_tokens` from the exact assembled context;
- preserve provider-native `input_tokens`, `output_tokens`, and `total_tokens`;
- derive `rag.system_tokens`, `rag.question_tokens`, and `rag.context_tokens` from the exact generator inputs with the model-appropriate tokenizer;
- use native span duration for stage latency and native root duration for total latency; do not log duplicate flat latency fields;
- allow exceptions inside instrumented spans so MLflow records native error status and exception information; do not label a low-quality answer as a technical error.

Capture actual runtime values, not the manifest's intended values, when logging request evidence.

### 8. Preserve evaluation semantics

If the repository already performs visible evaluation, keep one run per `candidate_id × corpus_id × evaluator_version` and log those immutable run parameters. Link run metrics to the candidate's native `model_id` where the active MLflow version supports it. Keep per-question scorer output on traces as assessments and aggregate numeric quality/efficiency results on the evaluation run as specified in the logging conventions.

Do not invent scorer definitions, evaluator versions, or quality metrics. Do not move held-out evidence into the development destination. Implement protected-destination selection only in the held-out path.

## Smoke verification

Run the smallest existing command that exercises one real RAG request; do not create a new test suite. Verify that:

- imports and application startup still succeed;
- the request result and existing RAG behavior are unchanged;
- required environment variables are validated and no configured resource or identity value is hardcoded in source;
- the selected workspace and existing experiment match the environment-provided values;
- only the RAG identity is used;
- candidate initialization creates or reuses the correct LoggedModel without changing the candidate ID;
- the candidate manifest hash matches `compute_config_hash()` and the LoggedModel carries `rag_type` and `config_hash`;
- the trace is linked to the native `model_id` and contains `candidate_id` and `corpus_id` metadata;
- applicable stages, token evidence, chunk counts, timings, and native errors follow the supplied conventions;
- no local MLflow server starts and no secret enters tracked files.

When remote credentials and model access are available, send one traced request and use `mlflow.search_traces()` in the configured workspace/experiment to retrieve it by native model association or correlation metadata. If access is unavailable, complete static integration and report exactly which remote checks remain unverified; do not weaken authentication or redirect traffic.

## Completion report

Report concisely:

1. dependency and configuration files changed;
2. candidate manifest/configuration source and resulting candidate ID/config hash;
3. automatic integration enabled and manual spans added;
4. environment variables required, without printing secrets;
5. smoke command run and whether the result passed;
6. whether LoggedModel creation/reuse and remote trace delivery were verified;
7. selected destination using environment-variable names and, only if safe, their non-secret resolved values;
8. any contract evidence that remains unavailable from the current RAG.

The work is complete when the existing baseline emits a candidate-linked, contract-compliant MLflow trace to the environment-selected central destination, or when all repository changes are complete and only unavailable external credentials/services prevent remote verification.

## Official references

- [Tracing integrations](https://mlflow.org/docs/latest/genai/tracing/integrations/)
- [Tracing quickstart](https://mlflow.org/docs/latest/genai/tracing/quickstart/)
- [Trace search](https://mlflow.org/docs/latest/genai/tracing/search-traces/)
- [Token usage](https://mlflow.org/docs/latest/genai/tracing/token-usage-cost/)
- [Application version tracking](https://mlflow.org/docs/latest/genai/version-tracking/)
- [MLflow Python API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.html)
- [Workspaces](https://mlflow.org/docs/latest/self-hosting/workspaces/)
- [Authentication and RBAC](https://mlflow.org/docs/latest/self-hosting/security/)

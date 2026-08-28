---
name: apply-rag-recommendations
description: Apply optimization recommendations to the Target RAG repository, assign the next Target RAG candidate ID, and optionally deploy and verify the resulting candidate. Use when Hermes has decided how the Target RAG should change and needs to implement that change. Do not redesign MLflow infrastructure, modify evaluation semantics, or use Hermes credentials to write Target RAG evidence.
---

# Apply Target RAG Recommendations

Implement an optimization recommendation in the Target RAG, create exactly one new behavioral candidate, and preserve the project's MLflow candidate and tracing contracts.

This skill is the external authority that assigns the next `candidate_NNN`. The Target RAG's own MLflow integration must continue treating `MLFLOW_CANDIDATE_ID` as externally supplied.

## Authoritative project contracts

Before editing the Target RAG, read completely:

1. `MLflow logging conventions.txt`
2. `.env.example`
3. `mlflow_rag_helpers.py`
4. `skills/add-rag-mlflow-tracing/SKILL.md`
5. `skills/add-hermes-mlflow-read-access/SKILL.md`

Do not duplicate or replace the responsibilities of `add-rag-mlflow-tracing`.

In particular:

- Hermes assigns the candidate ID.
- The Target RAG runtime owns Target RAG MLflow writes.
- Hermes may use `hermes-reader` to inspect existing candidate evidence.
- Hermes must never use `rag-service` credentials itself merely to create a candidate.
- The Target RAG's existing integration creates or safely reuses the candidate LoggedModel after receiving the new `MLFLOW_CANDIDATE_ID`.

## Required inputs

Before changing code, establish all of the following:

- the Target RAG repository or working-tree location;
- the recommendation or recommendation source to implement;
- the Target RAG deployment location and deployment mechanism;
- where the deployed Target RAG receives `MLFLOW_CANDIDATE_ID`.

Use information already present in the task or environment when available.

### Mandatory user handoff when information is missing

If the recommendation has not been supplied or its source is unknown, ask the user:

> How will I receive the Target RAG recommendations? For example: directly in the prompt, a file, another repository artifact, DeepFix output, or another source?

If the Target RAG deployment mechanism or candidate-ID configuration is unknown, ask the user:

> Where is the Target RAG deployed, how should I redeploy/restart it after a change, and where is `MLFLOW_CANDIDATE_ID` configured for that deployment?

If the Target RAG repository cannot be identified unambiguously from the current workspace, also ask for its repository/path and target branch or worktree.

Do not guess these integration boundaries.

## Scope

Allowed:

- inspect the Target RAG implementation and configuration;
- interpret a supplied optimization recommendation;
- modify Target RAG application code, prompts, or behavioral configuration needed to implement it;
- update tests directly affected by the change;
- assign the next `candidate_NNN`;
- update the deployment/runtime configuration that supplies `MLFLOW_CANDIDATE_ID`;
- execute the repository's existing tests and validation;
- deploy or restart the Target RAG when the deployment mechanism has been supplied and the current task permits it;
- use Hermes's read-only MLflow access to check existing candidate IDs and verify post-deployment evidence.

Out of scope:

- administer central MLflow;
- change MLflow RBAC;
- write Target RAG evidence using Hermes credentials;
- change evaluation datasets, scorers, thresholds, or hidden-evaluation logic unless separately requested;
- silently modify unrelated RAG behavior;
- combine unrelated recommendations into one candidate without explicit reason;
- weaken or bypass `add-rag-mlflow-tracing`;
- expose credentials or commit secrets.

## Workflow

### 1. Resolve the recommendation

Obtain the exact recommendation to implement.

Record a concise working statement containing:

- observed problem;
- proposed behavioral change;
- expected effect;
- files/components likely affected.

Do not expand a narrow recommendation into a general RAG refactor.

If several independent recommendations are supplied, prefer one candidate per independently testable behavioral change unless the recommendations explicitly form one coherent change set.

### 2. Inspect the Target RAG

Identify:

- application entry point;
- configuration system;
- prompt definitions;
- query processing and optional rewriting;
- retrieval;
- reranking;
- context assembly;
- generation;
- tests;
- MLflow integration;
- deployment configuration;
- current source of `MLFLOW_CANDIDATE_ID`.

Understand the actual runtime behavior before editing.

Do not infer behavior solely from filenames or documentation.

### 3. Determine the predecessor candidate

Determine the candidate currently represented by the deployed Target RAG.

Prefer, in order:

1. the deployment/runtime configuration actually supplying `MLFLOW_CANDIDATE_ID`;
2. deployment inspection through the supplied deployment mechanism;
3. explicit user-provided current candidate ID.

Hermes's read-only MLflow access may be used to cross-check that the candidate exists.

Do not infer the active predecessor merely by selecting the numerically largest candidate present in MLflow. Historical, abandoned, parallel, or failed candidates may exist.

### 4. Assign the next candidate ID

For an established improvement cycle:

```text
candidate_007 -> candidate_008
candidate_099 -> candidate_100
```

Preserve at least three numeric digits.

The new candidate must increment the predecessor candidate number by exactly one.

If transitioning from the baseline phase and there has never been a `candidate_NNN`, use:

```text
candidate_001
```

Do not convert `baseline_007` automatically into `candidate_008`; baseline numbering and candidate numbering are separate phases.

Before applying the new ID, use Hermes's read-only MLflow access when available to check whether that candidate name already exists.

If the proposed next candidate ID already belongs to another candidate:

- do not overwrite it;
- do not silently skip to a later number;
- stop and report the collision to the user.

Candidate assignment is part of this skill. The prohibition against auto-incrementing IDs inside `add-rag-mlflow-tracing` remains unchanged because the Target RAG integration is a consumer of the externally assigned ID, not its allocator.

### 5. Implement the recommendation

Make the smallest behavioral change that faithfully implements the recommendation.

Preserve unrelated behavior.

Follow the Target RAG repository's existing architecture, package manager, formatting, testing, and configuration conventions.

Do not alter MLflow tracing merely because RAG behavior changed unless the behavioral change introduces or removes an actual semantic RAG stage that requires corresponding contract instrumentation.

Examples:

- changing retrieval `top_k` should update the effective retrieval configuration;
- adding reranking must also make the reranking configuration and semantic stage accurate;
- changing a runtime prompt must change the effective prompt used by the RAG;
- changing generation parameters must be represented by the runtime configuration consumed by the candidate manifest.

Do not manually manufacture `config_hash`.

The Target RAG's MLflow integration must derive its new manifest, prompt-version references, and `config_hash` from the resulting effective runtime behavior.

### 6. Update the deployed candidate ID

Change the actual configuration source used by the deployment to:

```text
MLFLOW_CANDIDATE_ID=<new candidate ID>
```

Update only the authoritative runtime/deployment location.

Do not assume `.env`, `.env.example`, Docker Compose, Kubernetes, systemd, CI/CD variables, or another mechanism is authoritative until the deployment mechanism has been established.

Do not commit secrets.

Do not change the central `mlflow-deepfix/.env.example` merely to represent the currently deployed candidate.

### 7. Validate before deployment

Run the smallest relevant existing validation first, then broader tests when practical.

At minimum verify:

- changed code imports/builds;
- directly affected tests pass;
- the recommendation is actually reflected in runtime configuration;
- unrelated configuration has not changed unintentionally;
- Target RAG MLflow integration still initializes;
- the new candidate ID is supplied externally rather than hardcoded into application logic.

Inspect the final diff before deployment.

Remove accidental formatting, generated files, debugging code, temporary credentials, and unrelated edits.

### 8. Deploy or restart

If the user has supplied the deployment mechanism and the task permits deployment:

1. update the deployment candidate ID;
2. deploy/restart using the existing mechanism;
3. allow the Target RAG's own MLflow integration to initialize the new candidate;
4. do not create the LoggedModel through Hermes credentials.

If deployment is not currently possible, leave the repository in a validated state and report exactly what deployment action and candidate-ID update remain outstanding.

### 9. Verify the new candidate

When Hermes MLflow read access and a running Target RAG are available, verify:

- the expected `candidate_NNN` exists;
- its manifest reflects the intended behavioral change;
- its `config_hash` differs from the predecessor when behavior changed;
- request traces are associated with the new candidate;
- required semantic spans still conform to the tracing contract.

Do not interpret absence of traffic as an integration failure if no request has yet been sent.

When permitted, execute the smallest real smoke request needed to establish that the deployed candidate emits valid evidence.

### 10. Report

Report:

- recommendation applied;
- Target RAG repository/branch or worktree;
- files changed;
- predecessor candidate ID;
- new candidate ID;
- tests/validation performed;
- deployment/restart performed or still required;
- MLflow candidate/evidence verification performed;
- any unresolved issue.

Do not claim deployment, candidate registration, or MLflow verification succeeded unless it was actually observed.

## Candidate invariants

Always preserve these invariants:

```text
one behavioral candidate -> one candidate_NNN
candidate ID assigned by Hermes/control workflow
candidate ID consumed by Target RAG runtime
LoggedModel created/reused by Target RAG MLflow integration
Target RAG evidence written with rag-service identity
Hermes evidence inspection performed with hermes-reader identity
```

Never solve an ID collision by silently choosing a different candidate number.

Never increment the candidate number merely because the skill was rerun. Increment it only when creating a new behavioral candidate.
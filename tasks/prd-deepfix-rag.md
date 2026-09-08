# Product Requirements Document (PRD): deepfix-rag

**Document Version:** 1.0.0  
**Project Title:** deepfix-rag — Autonomous Diagnosis & Hyperparameter Optimization of Retrieval Models in RAG Systems  
**Collaboration Parties:** AI Service Center @ HPI (AISC) & Mittelstand Zukunftslab  
**Target Repository:** `pilotproject-deepfix`  
**Document Status:** Approved for Implementation (Aligned with Formal Pilot Project Agreement)  

---

## 1. Introduction & Overview

### 1.1 Problem Statement
Retrieval-Augmented Generation (RAG) pipelines in production and domain-specific settings (e.g., enterprise search, legal discovery, parliamentary analysis) suffer from silent, multi-modal failure states. Failures frequently originate from suboptimal document chunking, vocabulary mismatch, retriever confusion in large corpora, uncalibrated re-ranking thresholds, or improper deflection on unanswerable queries. Today, optimizing these parameters requires manual, ad-hoc experimentation by engineers.

### 1.2 The deepfix-rag Solution
Inspired by Andrej Karpathy's *AutoResearch* paradigm, **deepfix-rag** provides an autonomous framework for the continuous diagnosis and hyperparameter optimization (HPO) of multi-paradigm RAG systems. The fundamental architectural principle is the **strict decoupling of deterministic pipeline execution from agentic reasoning**:

1. **Deterministic Execution Layer (`deepfix-rag` CLI):** A standalone, scriptable Python CLI providing atomic, idempotent commands for dataset ingestion, indexing across multiple retrieval paradigms, failure-mode diagnostics, configuration mutation, and DeepEval evaluation. The CLI contains no agent logic or conversational state.
2. **Agentic Reasoning Layer (Skill Library & Hermes Harness):** Standardized failure remediation skills created under `.agents/skills/<skill-name>/SKILL.md` and packaged via declarative plugin manifest files (`plugin.json` / `plugin.yaml` under `plugins/deepfix-rag/`) conforming to **Claude Code**, **Hermes**, and **Codex** specifications. This manifest standard makes the packaged skills easily installable and portable from one coding harness to another, instructing autonomous agents (specifically Hermes running on self-hosted Qwen 3.8 27B via vLLM) on how to diagnose retrieval pathologies, formulate hypotheses, mutate configuration parameters, re-evaluate, and keep or revert changes in a closed feedback loop.
3. **Advisory & Expert Baseline Layer:** Domain validation and methodology oversight through coaching on evaluation design (Paras Mehta) and empirical comparison against an authentic parliamentary expert baseline from the German Bundestag (Mario Tormo Romero).

```
+-----------------------------------------------------------------------------------------+
|                                    ADVISORY & QA TRACK                                  |
|  - Evaluation Design Coaching (Paras Mehta)                                             |
|  - German Bundestag Parliamentary Expert Baseline & Sign-Off (Mario Tormo Romero)       |
+--------------------------------------------+--------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                           AGENTIC HARNESS & REASONING LAYER (Pair B)                    |
|  Hermes Agent Harness (vLLM / Qwen 3.8 27B on HPI VM)                                  |
|  Reads skills -> Analyzes Diagnostics -> Formulates Hypotheses -> Mutates Config       |
+--------------------------------------------+--------------------------------------------+
                                             |
                     [Subprocess / Terminal Tool Calls: `deepfix-rag --json`]
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                         WORKSPACE SKILLS & PLUGIN LAYER (Pair B)                        |
|  .agents/skills/ & plugins/deepfix-rag/                                                |
|  ├── .agents/skills/chunking-tuning/SKILL.md    ├── .agents/skills/deflection-and-refusal/SKILL.md |
|  ├── .agents/skills/query-rewriting/SKILL.md    ├── .agents/skills/legal-retrieval/SKILL.md    |
|  ├── .agents/skills/rerank-calibration/SKILL.md ├── .agents/skills/multi-hop-aggregation/SKILL.md |
|  └── .agents/skills/embedding-selection/SKILL.md                                       |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
|                        DETERMINISTIC CLI EXECUTION LAYER (Pair B)                       |
|  deepfix-rag (alias: deepfix)                                                           |
|  ├── dataset (load / generate-unanswerable) ├── run-diagnostic                          |
|  ├── index (--paradigm dense|hybrid|vectorless|graph)                                  |
|  ├── mutate-config / diff-config            ├── eval & export                           |
+---------------------+---------------------------------------------+---------------------+
                      |                                             |
                      v                                             v
+---------------------------------------+     +-------------------------------------------+
|    MULTI-PARADIGM RAG STACK (Pair A)   |     |       EVALUATION & TRACING (Pairs A & B)  |
|  LlamaIndex Orchestration:            |     |  - DeepEval (Faithfulness, Precision,     |
|  - Dense Vector: LanceDB + Octen 8B   |     |    Correctness, Deflection, Legal)        |
|    & qwen3-reranker-4b Cross-Encoder  |     |  - MLflow Auto-Tracing (Span Trees,       |
|  - Hybrid: BM25 + LanceDB Fusion      |     |    Run Metrics, Parameter Diffs)          |
|  - Vectorless: pageindex structural   |     |  - 6 Standard Text-Only Benchmarks Suites |
|  - Graph RAG: LightRAG Server (REST)  |     |                                           |
+---------------------------------------+     +-------------------------------------------+
```

---

## 2. Goals & Objectives

1. **Deterministic Execution:** Build a production-grade Python CLI (`deepfix-rag`, aliased as `deepfix`) using Typer, Pydantic, and Pydantic-Settings with strict schema validation, strongly-typed environment variable management, and guaranteed deterministic output formatted as machine-readable JSON (`--json`).
2. **Multi-Paradigm Retrieval:** Replace legacy pyTerrier implementations with four modern, production-grade retrieval paradigms orchestrated via LlamaIndex:
   - **Dense Vector:** LanceDB + `Octen 8B` (fine-tuned from `Qwen3-Embedding-8B`) with `qwen3-reranker-4b` for candidate re-ranking.
   - **Hybrid:** BM25 lexical search + LanceDB dense vector fusion.
   - **Vectorless:** `pageindex` structural and hierarchical document retrieval.
   - **Graph RAG:** `LightRAG Server` (prioritizing client/server REST API integration over embedding the heavy LightRAG SDK) for knowledge graph extraction and entity-relation retrieval.
3. **Standardized Skill Library & Plugin Manifest:** Create modular failure remediation skills under `.agents/skills/<skill-name>/SKILL.md` covering $\ge 5$ discrete retrieval failure modes, packaged with declarative manifest files (`plugin.json` / `plugin.yaml` in `plugins/deepfix-rag/`) conforming to **Claude Code**, **Hermes**, and **Codex** specifications, making skills seamlessly discoverable and installable from one coding harness to another to diagnose and heal RAG regressions.
4. **Autonomous Closed Loop:** Tune the Hermes agent harness running against self-hosted Qwen 3.8 27B on the HPI infrastructure to execute $\ge 5$ continuous optimization cycles autonomously, keeping mutations that improve DeepEval metrics and reverting degradations.
5. **Comprehensive Benchmarking & Expert Grounding:** Ingest 6 standardized text-only benchmark suites with strict `dev`/`test` split isolation, ensuring pipeline quality meets or exceeds strict quality gates and performs within $\le 10\%$ of the German Bundestag parliamentary expert baseline.
6. **Full Observability:** Track every agent iteration, configuration diff, diagnostic breakdown, and DeepEval evaluation score in a centralized MLflow tracking instance with complete hierarchical span trees.

---

## 3. Team Structure & Execution Tracks

Execution is divided into two operational development pairs and one advisory/quality layer, strictly adhering to the Pilot Project Agreement:

| Execution Track | Team Members | Core Scope & Responsibilities |
| :--- | :--- | :--- |
| **Track Pair A — RAG Multi-Paradigm Architectures, HPO & Evaluation** | **Ali Alhalabi**<br>**Agrim Sharma** | - Exploration and implementation of 4 RAG paradigms (Dense, Hybrid, Vectorless, Graph RAG) orchestrated via LlamaIndex.<br>- Benchmark dataset ingestion adapters, streaming loaders, and strict `dev`/`test` split management.<br>- RAG Hyperparameter Optimization (HPO) search space definitions.<br>- Execution and validation of DeepEval metric pipelines (Faithfulness, Contextual Precision, Correctness, Deflection, Legal Grounding). |
| **Track Pair B — Harness Engineering, Skill Plugins, CLI & Autonomous Loop** | **Dmitrii Karpov**<br>**Fadel Mamar Seydou**<br>**Tetyana Lytvyn** | - Development and packaging of the deterministic `deepfix-rag` CLI (Typer, Pydantic).<br>- Authoring modular `SKILL.md` files under `.agents/skills/` and plugin manifests in `plugins/deepfix-rag/`.<br>- Hermes agent harness integration, prompt templates, and shell tool routing.<br>- MLflow auto-tracing, telemetry span trees, and infrastructure DevOps.<br>- Autonomous closed-loop execution and optimization loop stability evaluation.<br>*(Tetyana contributes flexibly across Pair B development, tooling, and documentation).* |
| **Advisory & Quality Assurance Track** | **Paras Mehta**<br>**Mario Tormo Romero** | - **Paras Mehta (8 h/week):** Evaluation methodology coaching, metric sanity reviews, avoidance of RAG/agent evaluation pitfalls.<br>- **Mario Tormo Romero:** Provision of the German Bundestag parliamentary expert baseline and methodological review/sign-off on domain comparisons. |

---

## 4. Multi-Paradigm RAG Architecture (Pair A Scope)

Legacy references to pyTerrier are completely excised and superseded by four concrete, production-grade retrieval paradigms orchestrated through **LlamaIndex**. The CLI must expose paradigm selection via the `--paradigm` argument:

```bash
deepfix-rag index --dataset <path|name> --paradigm <dense|hybrid|vectorless|graph> --config <config.yaml>
```

### 4.1 Dense Vector Search & Re-Ranking
- **Vector Store:** LanceDB (embedded serverless vector storage with native Arrow support).
- **Embedding Model:** Self-hosted `Octen 8B` (fine-tuned from `Qwen3-Embedding-8B`) served via AISC / vLLM / LiteLLM proxy.
- **Cross-Encoder Re-Ranker:** `qwen3-reranker-4b` for precision candidate scoring and top-n truncation.
- **Characteristics:** Semantic similarity matching using cosine or HNSW index partitions combined with cross-encoder re-ranking; optimal for open-domain questions where semantic meaning transcends lexical phrasing.

### 4.2 Hybrid Search (Dense + Lexical Fusion)
- **Components:** BM25 (sparse lexical inverted index) fused with LanceDB dense vector embeddings.
- **Fusion Algorithm:** Reciprocal Rank Fusion (RRF) or relative score normalization with tunable balance parameter $\alpha \in [0.0, 1.0]$.
- **Characteristics:** Overcomes vocabulary mismatch and handles precise keyword matches (statute numbers, product codes, proper names) while retaining broad semantic context.

### 4.3 Vectorless Structural Retrieval (`pageindex`)
- **Mechanism:** Hierarchical, structural document indexing without floating-point dense embedding vectors.
- **Characteristics:** Leverages document layout structure, table of contents trees, section outlines, and semantic heading hierarchies.
- **Target Workload:** Highly structured enterprise manuals, legislative bodies of work, and long-form documents where spatial-structural location dictates relevancy.

### 4.4 Graph RAG (`LightRAG Server`)
- **Deployment Architecture:** Prioritizes interaction via the **LightRAG Server** REST API (hosted on the HPI VM or locally) rather than embedding the heavy LightRAG Python SDK into the CLI process. The CLI communicates via HTTP client endpoints (`/documents/text`, `/query`, etc.).
- **Architecture Benefits:** Decouples graph storage, asynchronous entity extraction, and graph traversal compute from the CLI client; eliminates heavy graph library dependency conflicts and isolates long-running background extraction.
- **Mechanism:** Dual-level knowledge graph extraction and retrieval using entity, relation, and passage graph networks.
- **Characteristics:** Extracts low-level entities and high-level relationships, enabling global query synthesis across disconnected documents.
- **Target Workload:** Multi-hop reasoning, thematic aggregation, and questions requiring comprehensive corpus-wide relationship traversal (EnterpriseRAG-Bench, BrowseComp-Plus).

---

## 5. Deterministic CLI & Agent Harness (Pair B Scope)

### 5.1 CLI Design Principles
- **Executable Identity:** Package name `deepfix_rag`, providing both `deepfix-rag` command and `deepfix` alias.
- **Framework & Settings:** Typer (Click-compatible) for terminal interaction, coupled with Pydantic for rigid YAML/JSON schema validation and **Pydantic-Settings** (`pydantic-settings`) for strongly-typed environment variable management (`.env` loading, API keys, endpoint URLs, provider selection).
- **Strict Determinism:** Identical inputs and configurations produce bit-for-bit identical index states and evaluation outputs.
- **Agent Interoperability:** All subcommands enforce an optional or default `--json` flag producing well-typed machine-readable JSON payloads on `stdout`. Runtime logs, debug traces, and progress bars are routed strictly to `stderr`.
- **Exit Code Convention:**
  - `0`: Success.
  - `1`: Validation or configuration schema error.
  - `2`: Runtime execution failure (network, out-of-memory, storage).

### 5.2 CLI Subcommand Specifications

#### 1. `deepfix-rag dataset`
- `deepfix-rag dataset load --name <garage|blleqa|legal-rag-bench|browsecomp-plus|enterpriserag|mtrag> --split <dev|test> [--stream] [--json]`
  - Loads benchmark dataset splits. `--stream` enables memory-efficient batch streaming for corpora $>500\text{k}$ documents (EnterpriseRAG-Bench).
  - Enforces strict isolation: optimization routines fail if `--split test` is requested during an agent loop.
- `deepfix-rag dataset generate-unanswerable --dataset <path> --ratio <0.2> --output <path> [--json]`
  - Generates synthetic out-of-domain and unanswerable queries for custom datasets to test deflection thresholds.

#### 2. `deepfix-rag index`
- `deepfix-rag index --dataset <path|preset> --paradigm <dense|hybrid|vectorless|graph> --config <config.yaml> --index-dir <dir> [--json]`
  - Builds the index using the selected paradigm via LlamaIndex orchestration. Outputs chunk count, index build duration, and disk footprint.

#### 3. `deepfix-rag eval`
- `deepfix-rag eval --dataset <path|preset> --split <dev|test> --config <config.yaml> [--paradigm <name>] [--json]`
  - Executes DeepEval metrics: Faithfulness, Contextual Precision, Answer Correctness, Deflection Rate, and Legal Error Decomposition. Automatically logs all scores and query traces to MLflow.

#### 4. `deepfix-rag run-diagnostic`
- `deepfix-rag run-diagnostic --dataset <path|preset> --config <config.yaml> [--type <retrieval|entity|ranking|grounding>] [--json]`
  - Performs fine-grained failure analysis on low-scoring queries and returns structured diagnostics categorizing failures into:
    1. **Retrieval Miss:** Gold passages absent from retrieved top-k chunks.
    2. **Reasoning Failure:** Gold passages retrieved, but generation model failed to synthesize correct response.
    3. **Hallucination / Grounding Violation:** Model generated statements contradicting context or hallucinated on unanswerable queries.

#### 5. `deepfix-rag mutate-config` & `diff-config`
- `deepfix-rag mutate-config --base <base.yaml> --set <key=value>... --output <new.yaml> [--json]`
  - Validates and applies atomic mutations against Pydantic schema (e.g., chunk size, overlap, top-k, RRF alpha, refusal threshold).
- `deepfix-rag diff-config <config_a.yaml> <config_b.yaml> [--json]`
  - Outputs a structured diff showing modified, added, or deleted hyperparameters.

#### 6. `deepfix-rag export`
- `deepfix-rag export --run-id <mlflow-run-id> --output-dir <path> [--json]`
  - Exports the champion RAG configuration YAML, evaluation scorecard, and reproduction script bundle.

### 5.3 Standardized Skill Library (`.agents/skills/`) & Plugin Manifest (`plugins/deepfix-rag/`)
To ensure complete portability and neutrality across agent coding harnesses, modular failure remediation skills are created natively under `.agents/skills/<skill-name>/SKILL.md` and packaged via a declarative manifest (`plugin.json` or `plugin.yaml` in `plugins/deepfix-rag/`) adhering to **Claude Code**, **Hermes**, and **Codex** plugin specifications:

```json
{
  "name": "deepfix-rag",
  "version": "1.0.0",
  "description": "Agent-agnostic RAG failure diagnosis, re-indexing, and configuration remediation skills.",
  "author": "Mittelstand Zukunftslab & AI Service Center @ HPI",
  "compatibility": {
    "claude_code": ">=1.0.0",
    "hermes": ">=2.0.0",
    "codex": ">=1.0.0"
  },
  "skills": [
    ".agents/skills/chunking-tuning/SKILL.md",
    ".agents/skills/query-rewriting/SKILL.md",
    ".agents/skills/rerank-calibration/SKILL.md",
    ".agents/skills/embedding-selection/SKILL.md",
    ".agents/skills/deflection-and-refusal/SKILL.md",
    ".agents/skills/legal-retrieval-grounding/SKILL.md",
    ".agents/skills/multi-hop-aggregation/SKILL.md"
  ],
  "tool_requirements": [
    {
      "command": "deepfix-rag",
      "purpose": "Deterministic CLI execution layer for RAG operations."
    }
  ]
}
```

Underneath this declarative manifest, each discrete failure remediation strategy is encapsulated as a standard Markdown document with YAML frontmatter located in `.agents/skills/<skill-name>/SKILL.md`:

```yaml
---
name: chunking-tuning
description: "Diagnose and remediate retrieval fragmentation or context overflow by optimizing chunk size, overlap, and splitting algorithms."
failure_mode: "context_fragmentation"
trigger_conditions:
  - "diagnostic.retrieval_miss_rate > 0.20"
  - "diagnostic.context_fragmentation == true"
cli_actions:
  - "deepfix-rag mutate-config --base configs/current.yaml --set retrieval.chunk_size=512 --set retrieval.chunk_overlap=64 --output configs/candidate.yaml"
  - "deepfix-rag index --config configs/candidate.yaml --paradigm dense"
  - "deepfix-rag eval --config configs/candidate.yaml"
---
```

**Cross-Harness Installation & Portability Benefits:**
- **Universal Discovery:** Claude Code, Hermes, or Codex harnesses discover the plugin via the manifest and register the skills from `.agents/skills/` into their runtime tool-calling context without code changes.
- **Uniform CLI Tooling:** The agent reads the declarative skill instructions and issues terminal tool calls against `deepfix-rag` in standard bash/zsh/powershell subprocesses.

**Required Skill Library Modules ($\ge 5$ Distinct Failure Modes):**
1. `chunking-tuning`: Tuning character/semantic splitters and chunk overlap to prevent context truncation.
2. `query-rewriting`: Handling multi-turn context drift, pronoun resolution, and hypothetical document embeddings (HyDE), especially for `mtRAG`.
3. `rerank-calibration`: Tuning cross-encoder re-ranking (`qwen3-reranker-4b`) top-n candidates, score thresholds, and dense/sparse balance.
4. `embedding-selection`: Evaluating semantic density versus exact token matching across domain corpora.
5. `deflection-and-refusal`: Calibrating confidence thresholds and system prompts to faithfully trigger "I do not have sufficient information" on unanswerable queries (`GaRAGe`, `bLLeQA`).
6. `legal-retrieval-grounding`: Optimizing hierarchical statute parsing, cross-reference expansion, and citation verification on `Legal RAG Bench`.
7. `multi-hop-aggregation`: Tuning retrieval depth and entity synthesis across disparate documents (`EnterpriseRAG-Bench`, `BrowseComp-Plus`).

### 5.4 Hermes Harness & Autonomous Closed Loop
- **Host Infrastructure:** Self-hosted `Qwen 3.8 27B` served via vLLM on HPI-provided VM.
- **Harness Integration:** Hermes agent configured with terminal execution tool calling.
- **Closed-Loop Workflow:**
  1. Hermes executes `deepfix-rag eval` to obtain baseline scores on `dev`.
  2. Hermes runs `deepfix-rag run-diagnostic --json` to inspect failure categorizations.
  3. Hermes reads matching `SKILL.md` from `.agents/skills/` based on diagnosed failure mode.
  4. Hermes formulates an optimization hypothesis and executes `deepfix-rag mutate-config`.
  5. Hermes executes `deepfix-rag index` (if indexing parameters changed) and `deepfix-rag eval`.
  6. **Quality Gate Decision:**
     - If DeepEval metrics improve: Candidate config becomes the new baseline.
     - If metrics degrade: Revert to previous baseline.
  7. Loop repeats for $\ge 5$ iterations autonomously. Every iteration logs a span tree to MLflow.

---

## 6. Benchmark Suite & Expert Baseline Matrix

DeepFix targets 6 standardized text-only benchmark suites covering distinct RAG challenges, evaluated strictly on held-out splits against established baselines:

| Benchmark Suite | Focus & Workload Characteristics | Target Optimization Challenge | Split Strategy |
| :--- | :--- | :--- | :--- |
| **GaRAGe** | Grounding & Factuality; ~2.3k queries with human-labeled relevant/unknown passages. | Hallucination suppression; tuning retrieval confidence thresholds for unanswerable queries. | 70% Dev (Optimization) / 30% Test (Locked) |
| **bLLeQA** | Multilingual (French/Dutch) legal domain Q&A with strict refusal requirements. | Cross-lingual retrieval alignment, legal terminology mismatch, and jurisdiction refusal. | 70% Dev / 30% Test |
| **Legal RAG Bench** (`isaacus/legal-rag-bench`) | Reasoning-intensive legal RAG with explicit error decomposition. | Separating retrieval misses from synthesis hallucinations; hierarchical statute chunking. | Dev / Test splits locked per benchmark spec |
| **BrowseComp-Plus** | Deep research reasoning over ~100k complex web documents. | Multi-evidence filtering and high-precision top-k candidate re-ranking. | Dev / Test splits locked per benchmark spec |
| **EnterpriseRAG-Bench** | Simulated enterprise knowledge base (>500k docs, low lexical overlap, multi-doc aggregation). | High-volume streaming, multi-hop document synthesis (up to 10 docs), enterprise jargon. | Dev / Test splits locked per benchmark spec |
| **mtRAG** | Multi-turn conversational dialogue (~7.7 turns/conversation) with ambiguous pronouns. | Maintaining conversational context state, resolving co-references, rewriting queries. | Dev / Test splits locked per benchmark spec |

### 6.1 German Bundestag Parliamentary Expert Baseline
- **Domain Scope:** Parliamentary documentation, legislative inquiries, and committee minutes.
- **Baseline Delivery:** Curated and signed off by domain expert **Mario Tormo Romero**.
- **Objective:** Validate that the autonomous optimization loop achieves performance within $\le 10\%$ of human expert performance on domain-specific retrieval and grounding tasks.

### 6.2 Evaluation Governance & Split Isolation
- **Strict Split Isolation:** The `dev` split is used exclusively during iterative agent optimization loops. The `test` split is encrypted or locked against agent read permissions and is evaluated only during final Sprint 4 verification.
- **Unweighted Scorecard:** In accordance with resolved architectural decisions, all benchmark scores (including multilingual bLLeQA and jurisdictional Legal RAG Bench) are reported independently without artificial metric weighting.

---

## 7. User Stories

### US-001: CLI Project Scaffolding & Unified JSON Interface
**Ownership:** `[Pair B]`  
**Description:** As a developer or agent harness, I want a unified CLI (`deepfix-rag` / `deepfix`) built with Typer and Pydantic that outputs structured JSON for all commands, so that programmatic callers parse results reliably.  
**Acceptance Criteria:**
- [ ] Both `deepfix-rag` and `deepfix` executable aliases function identically via `pyproject.toml` script entry points.
- [ ] Every CLI subcommand accepts `--json` and emits valid, schema-validated JSON to `stdout`.
- [ ] Informational logging, debug traces, and progress bars write exclusively to `stderr`.
- [ ] Standardized exit codes (`0` = success, `1` = schema/validation error, `2` = runtime exception) are returned.
- [ ] Full automated test suite passes typecheck and lint (`ruff check src`, `pytest tests/test_cli.py`).

### US-002: Benchmark Dataset Ingestion & Streaming Loaders
**Ownership:** `[Pair A]`  
**Description:** As a researcher or agent, I want standardized dataset loaders with memory-efficient streaming for large corpora, so that all 6 benchmark datasets can be ingested without out-of-memory errors.  
**Acceptance Criteria:**
- [ ] `deepfix-rag dataset load --name <dataset> --split <dev|test>` supports `garage`, `blleqa`, `legal-rag-bench`, `browsecomp-plus`, `enterpriserag`, and `mtrag`.
- [ ] Ingestion of `isaacus/legal-rag-bench` integrates directly with Hugging Face datasets.
- [ ] EnterpriseRAG-Bench (>500k documents) ingests smoothly via `--stream` in bounded memory (< 4GB RAM).
- [ ] Dev and test splits are saved into isolated, access-controlled directory paths (`data/processed/<dataset>/<split>`).
- [ ] Attempting to load `test` split during an active agent optimization session triggers an authorization rejection.

### US-003: Synthetic Unanswerable Query Generation
**Ownership:** `[Pair B]`  
**Description:** As an engineer optimizing refusal calibration, I want to automatically synthesize unanswerable/out-of-domain queries for custom corpora, so that deflection accuracy can be benchmarked.  
**Acceptance Criteria:**
- [ ] `deepfix-rag dataset generate-unanswerable --dataset <path> --ratio <float> --output <path>` executes deterministically.
- [ ] Generated unanswerable queries match the lexical distribution of the corpus but describe entities or facts absent from the ground-truth passages.
- [ ] Outputs standard JSONL dataset format (`queries.jsonl`, `qrels.jsonl`) with unanswerable ground-truth tags.

### US-004: Multi-Paradigm RAG Indexing & Retrieval Stack
**Ownership:** `[Pair A]`  
**Description:** As an agent or developer, I want to index and query documents across 4 distinct paradigms (Dense, Hybrid, Vectorless, Graph RAG) orchestrated via LlamaIndex, so that the optimal paradigm can be selected for each benchmark.  
**Acceptance Criteria:**
- [ ] `deepfix-rag index --paradigm dense` builds LanceDB vector index using `Octen 8B` (fine-tuned from `Qwen3-Embedding-8B`) with `qwen3-reranker-4b` re-ranking support.
- [ ] `deepfix-rag index --paradigm hybrid` builds and queries LanceDB dense vectors + BM25 sparse index with tunable RRF fusion.
- [ ] `deepfix-rag index --paradigm vectorless` builds and searches structural hierarchical trees via `pageindex`.
- [ ] `deepfix-rag index --paradigm graph` interfaces with a running **LightRAG Server** via REST API (prioritizing the client/server model over embedded SDK execution) for entity/knowledge graph extraction and graph retrieval.
- [ ] Index build metadata (document count, chunk statistics, build duration, storage bytes) is returned in `--json` output.
- [ ] All legacy pyTerrier dependencies and code references are completely removed.

### US-005: Failure Mode Diagnostics & Error Decomposition
**Ownership:** `[Pair B]`  
**Description:** As an autonomous agent, I want a diagnostic command that categorizes query failures into retrieval miss, reasoning error, or hallucination, so that I can select the correct remediation skill.  
**Acceptance Criteria:**
- [ ] `deepfix-rag run-diagnostic --dataset <path> --config <config.yaml> --json` evaluates sample queries.
- [ ] Diagnostic categorizes each query failure unambiguously:
  - `RETRIEVAL_MISS`: Target gold passage not retrieved in top-k.
  - `REASONING_FAILURE`: Target passage retrieved, but LLM response does not match ground truth.
  - `HALLUCINATION_DEFLECTION_FAILURE`: Model generated ungrounded assertions or answered an unanswerable query.
- [ ] Summary JSON contains aggregate pathology distributions across the evaluated dataset.

### US-006: Safe Configuration Mutation & Differential Analysis
**Ownership:** `[Pair B]`  
**Description:** As an autonomous agent, I want to programmatically mutate and diff RAG pipeline configurations with schema validation, so that parameter updates are valid and trackable.  
**Acceptance Criteria:**
- [ ] `deepfix-rag mutate-config --base <base.yaml> --set <key=value> --output <new.yaml>` validates updates against Pydantic model schemas.
- [ ] Invalid parameters (e.g., negative top-k, non-existent chunker algorithm) throw schema validation errors with exit code 1.
- [ ] `deepfix-rag diff-config <config_a.yaml> <config_b.yaml> --json` produces structured parameter diffs indicating additions, modifications, and deletions.

### US-007: Automated DeepEval Metric Pipeline Integration
**Ownership:** `[Pair A]`  
**Description:** As a developer or evaluation harness, I want to execute standardized DeepEval metrics against RAG outputs, so that quality indicators are objectively scored.  
**Acceptance Criteria:**
- [ ] `deepfix-rag eval --config <config.yaml> --dataset <dataset> --split dev` executes DeepEval metric suite.
- [ ] Pipeline computes Faithfulness ($\ge 0.80$), Contextual Precision ($\ge 0.75$), Answer Correctness ($\ge 75\%$), Deflection Accuracy ($\ge 80\%$), and Legal Error Reduction ($\ge 15\%$).
- [ ] Metric results, configuration parameters, and run metadata are automatically recorded in the central MLflow server.

### US-008: Workspace Skill Library (.agents/skills/) & Plugin Manifest
**Ownership:** `[Pair B]`  
**Description:** As an AI agent or harness engineer, I want modular `SKILL.md` files created under `.agents/skills/` and packaged under a declarative manifest (`plugin.json` / `plugin.yaml`) following Claude Code, Hermes, and Codex specifications, so that skills are directly accessible in the workspace and easily installable across coding harnesses.  
**Acceptance Criteria:**
- [ ] Declarative manifest file (`plugin.json` or `plugin.yaml`) authored in `plugins/deepfix-rag/` following Claude Code, Hermes, and Codex plugin schemas.
- [ ] Plugin is verified to be cleanly installable and discoverable across multiple agent harnesses without code modifications.
- [ ] Minimum of 5 modular skills created in `.agents/skills/<name>/SKILL.md`:
  - `chunking-tuning`
  - `query-rewriting` (mtRAG multi-turn context resolution)
  - `rerank-calibration`
  - `embedding-selection`
  - `deflection-and-refusal`
  - `legal-retrieval-grounding`
  - `multi-hop-aggregation`
- [ ] Each skill includes standard YAML frontmatter (`name`, `description`, `failure_mode`, `trigger_conditions`, `cli_actions`).
- [ ] Skills contain concrete, executable CLI command patterns and reasoning heuristics.

### US-009: Hermes Harness Integration & MLflow Tracing
**Ownership:** `[Pair B]`  
**Description:** As an infrastructure engineer, I want the Hermes agent harness running against self-hosted Qwen 3.8 27B on the HPI VM with full MLflow span tracking, so that all agent tool calls and reasoning steps are observable.  
**Acceptance Criteria:**
- [ ] Hermes harness executes shell commands via subprocess calling `deepfix-rag`.
- [ ] Inference endpoints for Qwen 3.8 27B, Octen 8B embeddings (fine-tuned from Qwen3-Embedding-8B), and `qwen3-reranker-4b` hosted via vLLM / LiteLLM on HPI VM are reachable and authenticated.
- [ ] Every agent execution cycle creates an MLflow run capturing system prompts, tool call payloads, stdout/stderr, and metric deltas in a hierarchical span tree.

### US-010: Closed-Loop Autonomous Optimization
**Ownership:** `[Pair B]`  
**Description:** As a pilot researcher, I want Hermes to execute multi-cycle optimization loops autonomously, keeping improvements and rolling back degradations, so that RAG pipelines self-heal without human intervention.  
**Acceptance Criteria:**
- [ ] Autonomous loop executes across $\ge 5$ sequential cycles on the `dev` split.
- [ ] $\ge 80\%$ of multi-cycle runs complete end-to-end without crashing, hanging, or requiring human input.
- [ ] Hermes successfully preserves candidate configurations when DeepEval scores increase, and restores previous configurations when scores decrease.
- [ ] Final best-performing configuration is exported using `deepfix-rag export`.

### US-011: Multi-Paradigm Hyperparameter Optimization (HPO) Space
**Ownership:** `[Pair A]`  
**Description:** As a RAG researcher, I want formal hyperparameter search spaces defined for each retrieval paradigm, so that the agent searches within valid and productive parameter boundaries.  
**Acceptance Criteria:**
- [ ] Pydantic schema defines parameter bounds for:
  - Dense: chunk size (128–2048), chunk overlap (0–256), top-k (1–50), similarity metric.
  - Hybrid: BM25 $k_1$ / $b$, RRF constant $c$, dense/sparse balance weight $\alpha \in [0, 1]$.
  - Vectorless: hierarchy tree depth (1–5), section summarization flags.
  - Graph RAG: entity extraction temperature, max hops (1–3), edge weight pruning thresholds.
- [ ] Invalid combinations are rejected during `mutate-config` validation.

### US-012: Evaluation Methodology Coaching & Quality Review
**Ownership:** `[Advisory]`  
**Description:** As an evaluation coach (Paras Mehta), I want to conduct sprint coaching and review evaluation notebook design, so that methodological errors in RAG and agent metrics are proactively prevented.  
**Acceptance Criteria:**
- [ ] Eval design coaching sessions conducted during Sprints 1, 2, and 4.
- [ ] Review and written sign-off provided on metric aggregation logic, deflection calculation formulas, and split leak prevention.

### US-013: German Bundestag Expert Baseline Comparison & Sign-Off
**Ownership:** `[Advisory]`  
**Description:** As a domain expert (Mario Tormo Romero), I want to benchmark optimized RAG outputs against the authentic German Bundestag parliamentary baseline, so that real-world domain fidelity is empirically verified.  
**Acceptance Criteria:**
- [ ] Bundestag expert baseline dataset and performance benchmarks provided by Sprint 2.
- [ ] Final Sprint 4 comparison table establishes whether `deepfix-rag` optimized pipelines perform within $\le 10\%$ of the expert baseline.
- [ ] Written review and sign-off on domain fidelity completed by Mario Tormo Romero.

---

## 8. Functional Requirements

### CLI & Deterministic Execution
- **FR-1:** The CLI executable must be named `deepfix-rag` with an operational alias `deepfix`.
- **FR-2:** All CLI subcommands must support a `--json` flag that emits strictly valid JSON to `stdout` containing machine-readable execution results.
- **FR-3:** All operational status updates, progress spinners, warnings, and error stacks must be emitted strictly to `stderr`.
- **FR-4:** The CLI must exit with status `0` on successful completion, `1` on invalid configuration/schema validation errors, and `2` on runtime/hardware execution errors.
- **FR-5:** The CLI must operate in non-interactive batch mode by default, supporting headless execution by agent subprocesses.

### Multi-Paradigm RAG Engine (Pair A)
- **FR-6:** The RAG retrieval engine must be orchestrated via LlamaIndex and support 4 distinct paradigms selectable via `--paradigm`:
  - `dense`: LanceDB vector storage with `Octen 8B` (fine-tuned from `Qwen3-Embedding-8B`) and `qwen3-reranker-4b` cross-encoder re-ranking.
  - `hybrid`: Dense LanceDB vectors fused with BM25 sparse keyword retrieval.
  - `vectorless`: Structural and hierarchical tree document retrieval via `pageindex`.
  - `graph`: Entity and relationship knowledge graph extraction and retrieval via **LightRAG Server** REST API (prioritizing server-based interaction over embedded SDK).
- **FR-7:** All legacy references and dependencies on `pyTerrier` must be purged and replaced with the LlamaIndex multi-paradigm stack.
- **FR-8:** The indexing subsystem (`deepfix-rag index`) must accept a configuration YAML file and document directory, generating the specified index deterministically.
- **FR-9:** The generation layer must query self-hosted `Qwen 3.8 27B` served via vLLM, supporting grounded prompt generation and token usage telemetry.

### Benchmark Data & Split Management (Pair A)
- **FR-10:** The dataset ingestion engine (`deepfix-rag dataset load`) must support 6 standardized benchmark suites: `garage`, `blleqa`, `legal-rag-bench`, `browsecomp-plus`, `enterpriserag`, and `mtrag`.
- **FR-11:** The dataset loader must support a `--stream` flag for memory-bounded streaming ingestion of large-scale corpora ($>500\text{k}$ documents).
- **FR-12:** The dataset subsystem must enforce strict split isolation: `dev` splits are accessible during autonomous agent optimization cycles; `test` splits are locked and access-restricted until final evaluation.
- **FR-13:** The dataset subsystem must provide `generate-unanswerable` to synthesize out-of-domain and unanswerable queries for custom datasets to test deflection accuracy.

### Diagnostics & Configuration Mutation (Pair B)
- **FR-14:** `deepfix-rag run-diagnostic` must analyze query evaluation failures and categorize each failure into one of three explicit classes: `RETRIEVAL_MISS`, `REASONING_FAILURE`, or `HALLUCINATION_DEFLECTION_FAILURE`.
- **FR-15:** `deepfix-rag mutate-config` must validate proposed parameter changes against a Pydantic schema model and output an updated, valid configuration YAML file.
- **FR-16:** `deepfix-rag diff-config` must compare two configuration YAML files and output a structural difference report in JSON or human-readable format.
- **FR-17:** `deepfix-rag export` must bundle the champion configuration YAML, benchmark scorecards, and reproduction instructions into a designated export directory.

### Skill Plugins & Agent Harness (Pair B)
- **FR-18:** The framework must maintain modular failure remediation skills under `.agents/skills/<skill-name>/SKILL.md` with standard YAML frontmatter, packaged via a declarative manifest (`plugins/deepfix-rag/plugin.json` or `plugin.yaml`) conforming to Claude Code, Hermes, and Codex specifications to enable automated installation and skill discovery across multiple coding harnesses.
- **FR-19:** The skill plugin must provide at least 5 distinct failure remediation skills covering: chunking strategy, query rewriting (including multi-turn dialogue), re-ranker calibration, embedding model selection, unanswerable deflection/refusal, legal retrieval grounding, and multi-hop aggregation.
- **FR-20:** The Hermes agent harness must integrate terminal subprocess tool calling to execute `deepfix-rag` subcommands autonomously.
- **FR-21:** The system must implement an autonomous closed-loop optimization cycle: evaluate baseline $\rightarrow$ run diagnostics $\rightarrow$ consult skill $\rightarrow$ mutate config $\rightarrow$ re-index/re-evaluate $\rightarrow$ keep improvement or revert degradation.
- **FR-22:** The autonomous loop must execute for $\ge 5$ iterative cycles without requiring human intervention.

### Observability & Tracing (Pair B)
- **FR-23:** All evaluation runs, configuration mutations, diagnostic summaries, and agent reasoning steps must be logged to a central MLflow tracking server on the HPI infrastructure.
- **FR-24:** MLflow logging must capture full hierarchical span trees for Hermes agent tool execution and link all runs to configuration artifact revisions.

### Evaluation & Expert Baseline (Pair A & Advisory)
- **FR-25:** The evaluation subsystem (`deepfix-rag eval`) must integrate DeepEval to compute: Faithfulness, Contextual Precision, Answer Correctness, Deflection/Refusal Accuracy, and Legal Error Decomposition.
- **FR-26:** The evaluation scorecard must report benchmark scores independently without artificial metric weighting between general English, multilingual, and legal datasets.
- **FR-27:** The system must support comparison against the German Bundestag parliamentary expert baseline provided by Mario Tormo Romero.

---

## 9. Non-Goals (Out of Scope)

To maintain focus and adhere to the formal Pilot Project Agreement, the following areas are explicitly excluded:
- **Commercial Production Systems:** The project delivers a research prototype, deterministic CLI, and agent skill plugin; it does not build proprietary SaaS commercial platforms.
- **Multimodal RAG:** Video, audio, and image retrieval pipelines are strictly excluded; the scope is text-only RAG.
- **Generative LLM Weight Fine-Tuning:** Direct fine-tuning (LoRA, full-parameter training) of underlying LLMs (Qwen 3.8 27B) is out of scope; optimization is confined to retrieval pipeline architecture, indexing configurations, and prompt hyperparameters.
- **Cloud-Only SaaS Infrastructure:** The system is built to execute in local developer environments and on HPI-hosted Linux VMs, not proprietary closed-cloud platforms.

---

## 10. Technical Architecture & Stack Specifications

### 10.1 Technology Stack Matrix

| Component | Technology / Library | Version / Constraint | Rationale |
| :--- | :--- | :--- | :--- |
| **Runtime Language** | Python | `~=3.12.0` | Modern type hint support, async performance, compatibility with modern RAG stack. |
| **CLI Framework** | Typer / Rich | `typer>=0.9.0` | Clean terminal ergonomics, autocompletion, automated `--help`, Click interoperability. |
| **Data Validation** | Pydantic | `>=2.0` | Rigid schema validation for RAG configurations, mutations, and JSON output models. |
| **Settings & Environment** | Pydantic-Settings | `>=2.0` | Strongly-typed environment variable management (`.env` loading, API keys, endpoint URLs, provider routing). |
| **RAG Orchestration** | LlamaIndex | `>=0.10.0` | Modular orchestration engine supporting dense, hybrid, vectorless, and graph retrievers. |
| **Vector Storage** | LanceDB | `>=0.37.1,<0.38.0` | High-performance serverless embedded vector storage with native Arrow and disk streaming. |
| **Dense Embeddings** | Octen 8B (fine-tuned from Qwen3-Embedding-8B) | Self-hosted AISC / vLLM | High-fidelity dense semantic embeddings served on local HPI VM infrastructure. |
| **Cross-Encoder Re-Ranker** | qwen3-reranker-4b | Self-hosted vLLM / OpenRouter / AISC | Cross-encoder re-ranking model for candidate scoring and precision filtering. |
| **Lexical Search** | BM25 (rank-bm25 / LanceDB FTS) | Integrated | Fast, accurate sparse keyword inverted index for hybrid fusion. |
| **Vectorless Search** | `pageindex` | Latest stable | Hierarchical, structural document indexing without dense embeddings. |
| **Graph RAG** | `LightRAG Server` | REST API / Latest | Dedicated knowledge graph service for multi-hop entity reasoning, accessed via REST endpoints instead of embedded SDK. |
| **Generation Model** | Qwen 3.8 27B | vLLM / LiteLLM Proxy | State-of-the-art open-weights reasoning model for agent harness and RAG synthesis. |
| **Agent Harness** | Hermes Agent | Terminal Subprocess | Autonomous tool-calling agent executing `deepfix-rag` terminal commands. |
| **Evaluation Suite** | DeepEval | `>=1.0.0` | Comprehensive RAG metric suite (Faithfulness, Precision, Correctness, Deflection). |
| **Experiment Tracking** | MLflow | `>=2.15.0` | Centralized tracking server for runs, metric scalars, configuration artifacts, and span trees. |
| **Benchmarking Corpora** | Hugging Face `datasets` | `>=2.14.0` | Standardized dataset access for legal, enterprise, conversational, and grounding suites. |

### 10.2 Hardware & Environment Specifications
- **Host Infrastructure:** HPI-hosted Linux VM with GPU acceleration.
- **Inference & Graph Servers:** 
  - vLLM / AISC instance serving `Qwen 3.8 27B` (generation/agent), `Octen 8B` (embeddings, fine-tuned from `Qwen3-Embedding-8B`), and `qwen3-reranker-4b` (re-ranking).
  - Dedicated `LightRAG Server` process exposing REST endpoints for graph extraction, entity indexing, and multi-hop graph queries.
- **Tracking Server:** MLflow tracking instance deployed on the same HPI VM.
- **Storage Layout:**
  - Raw/processed benchmark caches: `~/.deepfix/datasets/`
  - Index storage (LanceDB, graph DBs): `~/.deepfix/indices/`
  - Configuration revisions & exports: `configs/`, `exports/`

---

## 11. Success Metrics & Quality Gates

The project must satisfy the following verifiable quantitative targets and quality gates before pilot completion:

| Dimension | Metric / Indicator | Target Threshold | Verification Method |
| :--- | :--- | :--- | :--- |
| **RAG Quality** | **Faithfulness Score** | $\ge 0.80$ (scale 0.0–1.0) | DeepEval evaluation on held-out benchmark test sets. |
| **RAG Quality** | **Contextual Precision** | $\ge 0.75$ | DeepEval metric verifying that retrieved chunks contain relevant ground-truth evidence. |
| **RAG Quality** | **Answer Correctness** | $\ge 75\%$ | DeepEval semantic and factual similarity against ground-truth answers. |
| **RAG Quality** | **Deflection / Refusal Accuracy** | $\ge 80\%$ | Measured on unanswerable query subsets of `GaRAGe`, `bLLeQA`, and `EnterpriseRAG-Bench`. |
| **Domain Accuracy** | **Legal Retrieval Grounding** | $\ge 15\%$ reduction | Reduction in retrieval-induced reasoning errors on `Legal RAG Bench`. |
| **Expert Baseline** | **Bundestag Parliamentary Benchmark** | $\le 10\%$ delta | Performance on held-out Bundestag test set within $\le 10\%$ of expert baseline signed off by Mario Tormo Romero. |
| **Agent Skills** | **Failure Modes Remediated** | $\ge 5$ skills | Implemented modular `SKILL.md` plugins in `.agents/skills/`. |
| **Autonomy** | **Closed-Loop Stability** | $\ge 80\%$ completion | Percentage of autonomous multi-cycle optimization loops completing $\ge 5$ cycles without human intervention. |

---

## 12. Sprint Schedule & Deliverables Timeline

The 9-week project execution timeline follows a strict two-week sprint cadence governed by GitHub Issues and branch isolation:

### Sprint 1: Foundation, Baseline RAG & Initial Skills (Aug 17 – Aug 29)
- **Pair B:** Scaffold `deepfix-rag` CLI project (Typer, Pydantic); implement `dataset` and `index` subcommands with `--json`.
- **Pair B:** Implement `run-diagnostic` subcommand with retrieval failure categorization.
- **Pair B:** Deploy central MLflow tracking server on HPI VM.
- **Pair A:** Ingest initial benchmark datasets (`GaRAGe`, `bLLeQA`, `Legal RAG Bench`); establish and lock `dev`/`test` split boundaries.
- **Pair A:** Deploy and benchmark baseline RAG pipeline (Dense vector LanceDB + `Octen 8B` and Hybrid search with `qwen3-reranker-4b`).
- **Pair B:** Configure Hermes agent harness and author initial `SKILL.md` documents under `.agents/skills/`.
- **Pair A:** Perform exploratory data analysis (EDA) on `dev` splits.
- **Advisory:** Kick-off coaching call with Paras Mehta; alignment on Bundestag baseline with Mario Tormo Romero.

### Sprint 2: First Autonomous Solution (v1) & Multi-Paradigm Extensions (Sep 1 – Sep 12)
- **Pair B:** Implement v1 of closed-loop autonomous optimization loop using `deepfix-rag` CLI.
- **Pair B:** Implement `mutate-config`, `diff-config`, and `eval` subcommands.
- **Pair B:** Configure MLflow auto-tracing for all Hermes tool-calling runs.
- **Pair A:** Implement Vectorless (`pageindex`) and Graph RAG (`LightRAG Server` REST adapter) pipeline paradigms via LlamaIndex.
- **Pair A:** Define hyperparameter search spaces across all 4 paradigms.
- **Advisory:** Mario Tormo Romero provides German Bundestag expert baseline scores and evaluation alignment.

### Sprint 3: Progress-Driven Skills Expansion (Sep 15 – Sep 26)
- **Pair B:** Expand `.agents/skills/` library to cover $\ge 5$ distinct failure modes and verify plugin manifest.
- **Pair A:** Optimize cross-encoder re-ranking thresholds, multi-hop aggregation, and refusal prompts on `dev` splits.
- **Pair B:** Refine diagnostic categorization accuracy based on empirical error logs.
- **Advisory:** Paras Mehta conducts mid-point evaluation methodology review to prevent metric distortion.

### Sprint 4: Full Autonomous Loop & Final Benchmarking (Sep 29 – Oct 10)
- **Pair B:** Execute full autonomous closed-loop optimization ($\ge 5$ continuous cycles) across all benchmark domains.
- **Pair B:** Perform complete CLI end-to-end integration testing and reproducibility checks.
- **Pair A:** Execute final RAG evaluation on locked, held-out test splits across all 6 benchmark suites.
- **Advisory & Team:** Compile Bundestag expert baseline comparison table; sign-off by Mario Tormo Romero.
- **Pair B & Advisory:** Author autonomous optimization evaluation report with exported MLflow charts.
- **Advisory:** Final coaching review and methodology sign-off by Paras Mehta.

### Wrap-Up & Public Release (Oct 13 – Oct 15)
- Final evaluation debrief meeting with AI Service Center @ HPI.
- Polish repository documentation, clean configuration templates, and finalize `README.md`.
- Draft Pilot Project Report outline for submission.

---

## 13. Risks, Assumptions & Mitigation Strategies

### Key Mitigations
1. **Scope Creep in Skill Development:**
   - *Risk:* Over-engineering dozens of niche retrieval skills delays autonomous loop completion.
   - *Mitigation:* Enforce strict skill freeze at end of Sprint 2, prioritizing the 5 highest-impact failure modes.
2. **Benchmark Data Contamination & Overfitting:**
   - *Risk:* Agent loop optimizes against test queries, corrupting final benchmark validity.
   - *Mitigation:* Cryptographically isolate and lock `test` splits in Sprint 1; CLI rejects optimization commands targeting `test`.
3. **VM Infrastructure & Inference Instability:**
   - *Risk:* vLLM crashes or network timeouts break multi-cycle agent runs.
   - *Mitigation:* Implement exponential backoff, retry wrappers, and health check probes in LiteLLM / Hermes tool execution.

### Core Assumptions
- **Model Reasoning:** `Qwen 3.8 27B` provides adequate reasoning and tool-calling fidelity to parse diagnostic JSON and follow `SKILL.md` instructions.
- **Harness Adaptability:** Hermes agent harness supports terminal tool calling against local CLI executables without proprietary cloud dependencies.
- **Benchmark Validity:** The 6 benchmark suites represent authentic text-only enterprise and legal retrieval challenges.
- **CLI Determinism:** RAG indexing and DeepEval scoring produce bit-for-bit identical outputs when supplied with identical inputs and configuration YAMLs.

---

## 14. Resolved Architectural Decisions

1. **Deterministic Separation over Integrated Agents:**
   - *Decision:* The CLI remains completely deterministic and agent-neutral. The agent logic resides solely in the harness and skill plugin files.
2. **Cross-Harness Plugin Standards & Canonical `.agents/skills/` Root:**
   - *Decision:* Maintain the canonical skill definitions directly under `.agents/skills/<skill-name>/SKILL.md` conforming to workspace agent standards, packaged via declarative manifest files (`plugin.json` / `plugin.yaml` in `plugins/deepfix-rag/`) conforming to Claude Code, Hermes, and Codex specifications. This guarantees native workspace discovery as well as cross-harness portability and effortless installation from one coding agent harness to another without altering skill definitions or execution semantics.
3. **Replacement of Legacy pyTerrier & Multi-Paradigm Selection:**
   - *Decision:* Purge all legacy pyTerrier code and dependencies in favor of LlamaIndex orchestrating 4 concrete paradigms: LanceDB Dense (Octen 8B + `qwen3-reranker-4b`), Hybrid (BM25 + LanceDB), `pageindex` Vectorless, and `LightRAG Server` Graph RAG.
4. **LightRAG Server over Embedded SDK:**
   - *Decision:* Deploy LightRAG as a standalone service (**LightRAG Server**) communicating via REST API endpoints rather than embedding the heavy LightRAG Python SDK directly within the `deepfix-rag` CLI. This eliminates dependency bloat/conflicts, isolates long-running asynchronous graph extraction jobs, and enables daemonized graph management on the HPI VM.
5. **Streaming Ingestion for Massive Corpora:**
   - *Decision:* Build first-class `--stream` chunked loading into `deepfix-rag dataset load` to handle datasets $>500\text{k}$ documents (EnterpriseRAG-Bench) in bounded memory.
6. **Synthetic Deflection Generation:**
   - *Decision:* Provide `deepfix-rag dataset generate-unanswerable` directly within the CLI to enable robust refusal threshold calibration.
7. **No Metric Weighting Across Domains:**
   - *Decision:* General English, multilingual (`bLLeQA`), and legal (`Legal RAG Bench`) benchmarks are evaluated as raw, unweighted scorecards to ensure transparency and prevent artificial smoothing of domain performance.

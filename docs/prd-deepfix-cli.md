# PRD: DeepFix Optimization CLI & Agent-Agnostic Skill Plugin

## 1. Introduction/Overview

DeepFix is an agentic framework designed for the autonomous diagnosis and optimization of Retrieval-Augmented Generation (RAG) pipelines. To ensure modularity, reproducibility, and agent neutrality, the architecture decouples deterministic RAG pipeline operations from agentic reasoning:

1. **Deterministic Python CLI (`deepfix`)**: Exposes discrete, scriptable CLI subcommands (`dataset`, `index`, `eval`, `mutate-config`, `run-diagnostic`, `export`) that perform isolated operations on datasets, indexes, configurations, and evaluation suites without containing agent logic.
2. **Agent-Agnostic Skill Plugin**: A standardized plugin packaging modular `SKILL.md` files categorized by failure modes (e.g., query rewriting, chunking strategy tuning, re-ranking calibration, embedding model selection, unanswerable deflection, legal error decomposition). Any agent harness (such as Hermes, Claude, or Antigravity) reads these skills and executes terminal tool calls against the `deepfix` CLI.
3. **Hermes Integration & Benchmark Testing**: The system is validated and benchmarked with the Hermes agent executing autonomous multi-cycle optimization loops over standardized text-only RAG benchmark suites spanning:
   - **Complex Grounding & Domain-Specific Law**: GaRAGe, bLLeQA, and **Legal RAG Bench (`isaacus/legal-rag-bench`)**.
   - **Deep Research Reasoning**: BrowseComp-Plus (~100K web docs).
   - **Enterprise Simulation**: EnterpriseRAG-Bench (>500K docs, low lexical overlap, multi-doc aggregation).
   - **Multi-Turn Conversational AI**: mtRAG (multi-turn dialogue context maintenance).

---

## 2. Goals

- Provide a standalone, deterministic Python CLI (`deepfix`) providing granular RAG execution, dataset loading, and diagnostic tools.
- Support first-class ingestion, indexing, and evaluation adapters for key RAG benchmark datasets:
  - **GaRAGe**: Grounding, factuality, and deflection ("I don't know") evaluation.
  - **bLLeQA**: Multilingual (FR/NL) legal domain grounding and refusal testing.
  - **Legal RAG Bench (`isaacus/legal-rag-bench`)**: Reasoning-intensive legal benchmark decomposing RAG errors into retrieval failures vs. reasoning failures vs. hallucinations.
  - **BrowseComp-Plus**: Deep research reasoning across large-scale web document collections (~100K docs).
  - **EnterpriseRAG-Bench**: Enterprise knowledge base simulation (>500K docs, low lexical overlap, multi-doc aggregation).
  - **mtRAG**: Multi-turn dialogue, conversational context maintenance, and unanswerable query handling.
- Provide built-in streaming dataset ingestion for large corpora and synthetic unanswerable query generation for custom user datasets.
- Package an agent-agnostic plugin with modular `SKILL.md` documentation that guides AI agents on diagnosing and remediating specific RAG retrieval failure modes.
- Enable AI agents (specifically Hermes) to autonomously discover, run diagnostic checks, adjust RAG configurations, and re-evaluate via terminal tool calls.
- Track all agent-initiated optimization runs, config mutations, and DeepEval benchmark metrics in MLflow.
- Meet or exceed target RAG metrics (Faithfulness $\ge 0.80$, Contextual Precision $\ge 0.75$, Answer Correctness $\ge 75\%$, Deflection/Refusal Accuracy $\ge 80\%$) across at least 5 distinct failure remediation skills.

---

## 3. User Stories

### US-001: Standardized Benchmark Dataset Ingestion, Streaming & Synthetic Deflection Generation
**Description:** As a developer or agent, I want to load standard RAG benchmark datasets with streaming support and generate synthetic unanswerable queries so that optimization loops run efficiently and test deflection accurately.

**Acceptance Criteria:**
- [ ] `deepfix dataset load --name <garage|blleqa|legal-rag-bench|browsecomp-plus|enterpriserag|mtrag> --split <dev|test>` downloads/loads the dataset and outputs normalized corpus, query, and ground-truth metadata.
- [ ] Supports `--stream` flag on `deepfix dataset load` to stream and process large enterprise datasets (>500K documents) without excessive memory usage.
- [ ] Supports Hugging Face dataset loading for `isaacus/legal-rag-bench` directly via the dataset adapter.
- [ ] `deepfix dataset generate-unanswerable --dataset <path> --ratio <0.2> --output <path>` generates synthetic out-of-domain / unanswerable queries for custom datasets to test refusal calibration.
- [ ] Enforces strict split isolation: optimization cycles only execute against `dev`; `test` is locked for final evaluation.
- [ ] Supports custom dataset ingestion via standardized JSON Lines format (`corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`).

### US-002: Execute Baseline RAG Pipeline & Diagnostics via CLI
**Description:** As a developer or agent, I want to run deterministic CLI subcommands to evaluate a baseline RAG dataset and generate diagnostic failure logs.

**Acceptance Criteria:**
- [ ] `deepfix eval --dataset <path|preset> --config <config.yaml>` computes DeepEval metrics (Faithfulness, Context Precision, Correctness, Deflection Rate) and outputs JSON results.
- [ ] `deepfix run-diagnostic --dataset <path|preset> --type <retrieval|entity|ranking|grounding>` outputs structured diagnostic logs identifying low-scoring queries and failure categories (distinguishing retrieval failure vs. reasoning failure vs. hallucination).
- [ ] Results and artifact paths are automatically logged to the local/remote MLflow experiment tracking server.
- [ ] CLI exits with code 0 on success and non-zero on failure with clear error messages.

### US-003: Deterministic RAG Configuration Mutation via CLI
**Description:** As an agent executing remediation steps, I want to programmatically mutate RAG pipeline hyperparameters (chunk size, overlap, embedding model, top-k, re-ranker threshold, refusal/deflection prompt rules) via CLI commands.

**Acceptance Criteria:**
- [ ] `deepfix mutate-config --base <base.yaml> --set <key=value> --output <new_config.yaml>` validates and applies parameter changes.
- [ ] CLI enforces schema validation on parameter values (e.g. valid chunking algorithms, supported embedding models, threshold cutoffs).
- [ ] `deepfix diff-config <config_a.yaml> <config_b.yaml>` outputs a human/agent-readable diff of pipeline parameters.

### US-004: Re-indexing and Retriever Calibration via CLI
**Description:** As an agent testing new indexing or retrieval strategies, I want to trigger re-indexing and retrieval pipeline builds deterministically.

**Acceptance Criteria:**
- [ ] `deepfix index --dataset <path|preset> --config <config.yaml> --index-dir <dir>` builds the vector/lexical index using pyTerrier and specified embedding models (e.g., Qwen3-Embedding-4B, legal-specific dense models, BM25, Hybrid).
- [ ] Handles large-scale corpora efficiently via batch streaming (e.g. EnterpriseRAG-Bench >500K docs, BrowseComp-Plus ~100K docs).
- [ ] Re-indexing status, duration, and chunk statistics are printed in stdout in standard JSON/text format.

### US-005: Agent-Agnostic Plugin & Failure-Mode Skill Library
**Description:** As an AI agent (Hermes), I want clear, modular `SKILL.md` files for specific retrieval and grounding failure modes so that I can determine which `deepfix` CLI commands to execute.

**Acceptance Criteria:**
- [ ] Plugin structure created under `plugins/deepfix/` with `plugin.json` declaring skill namespaces.
- [ ] Modular skills created with standard YAML frontmatter, failure trigger conditions, and step-by-step CLI tool instructions:
  - `skills/chunking-tuning/SKILL.md` (tuning chunk sizes/splitters based on context fragmentation).
  - `skills/query-rewriting/SKILL.md` (diagnosing vocabulary mismatch and conversational multi-turn context resolution for **mtRAG**).
  - `skills/rerank-calibration/SKILL.md` (tuning cross-encoder re-ranking top-n and score cutoffs for dense/sparse fusion).
  - `skills/embedding-model-selection/SKILL.md` (evaluating dense vs. sparse vs. hybrid retrieval trade-offs across large enterprise and domain corpora).
  - `skills/deflection-and-refusal/SKILL.md` (calibrating retrieval confidence thresholds to trigger faithful refusal on unanswerable queries for **GaRAGe** & **bLLeQA**).
  - `skills/legal-retrieval-grounding/SKILL.md` (optimizing citation precision, hierarchical legal statute parsing, and mitigating false-hallucinations caused by retrieval misses on **Legal RAG Bench**).
  - `skills/multi-hop-aggregation/SKILL.md` (tuning retriever depth and aggregation strategies for complex multi-doc reasoning in **EnterpriseRAG-Bench** & **BrowseComp-Plus**).

### US-006: Autonomous Loop Validation with Hermes Agent
**Description:** As a researcher, I want to run and test the Hermes agent harness with the DeepFix plugin so that the agent performs $\ge 5$ autonomous diagnostic and remediation cycles across the benchmark suites.

**Acceptance Criteria:**
- [ ] Hermes agent harness is configured with shell/terminal execution capabilities.
- [ ] Hermes successfully reads the DeepFix skills, inspects initial `deepfix run-diagnostic` output, and executes appropriate `deepfix mutate-config`, `deepfix index`, and `deepfix eval` commands.
- [ ] Complete span tree and multi-turn conversation logs are captured in MLflow.
- [ ] Autonomous optimization completes $\ge 5$ iterative cycles without fatal script crashes or invalid command loops.

---

## 4. Functional Requirements

- **FR-1:** The `deepfix` Python CLI must provide the following core deterministic subcommands:
  - `deepfix dataset`: Ingest, split, stream (`--stream`), and synthesize deflection test queries (`generate-unanswerable`) for benchmark datasets (`garage`, `blleqa`, `legal-rag-bench`, `browsecomp-plus`, `enterpriserag`, `mtrag`, or custom paths).
  - `deepfix index`: Index documents with designated embedding model, chunking parameters, and vector store/pyTerrier index.
  - `deepfix eval`: Run DeepEval evaluation metrics against query-ground truth pairs.
  - `deepfix run-diagnostic`: Run IR diagnostic checks and categorize failure modes (e.g. low recall, precision drop, out-of-domain terms, retrieval vs. reasoning error decomposition, hallucination on unanswerable queries).
  - `deepfix mutate-config`: Validate and create updated RAG configuration files.
  - `deepfix diff-config`: Generate structural diffs between two configuration revisions.
  - `deepfix export`: Export final best-performing RAG configuration and benchmark report.
- **FR-2:** All CLI subcommands must support a `--json` output flag to output structured machine-readable responses suitable for agent parsing.
- **FR-3:** The CLI must integrate with MLflow for experiment tracking, logging metric scalars, artifacts, and configuration YAMLs per evaluation run.
- **FR-4:** The Agent-Agnostic Plugin must follow standard skill folder conventions (`skills/<skill-name>/SKILL.md`) with explicit trigger descriptions and tool call examples.
- **FR-5:** The skills must instruct the agent on the end-to-end loop:
  1. Evaluate current baseline (`deepfix eval`).
  2. Diagnose failure modes (`deepfix run-diagnostic`).
  3. Formulate optimization hypothesis based on corresponding `SKILL.md`.
  4. Mutate pipeline configuration (`deepfix mutate-config`).
  5. Re-index / re-run retrieval if necessary (`deepfix index`).
  6. Re-evaluate pipeline and compare scores (`deepfix eval`).
  7. Keep mutation if metrics improve; revert if metrics degrade.
- **FR-6:** The CLI must operate in non-interactive batch mode by default, supporting headless execution by agent subprocesses.

---

## 5. Benchmark Dataset Taxonomy & Workload Characteristics

DeepFix incorporates a diverse suite of text-only RAG benchmark datasets categorized by real-world challenges:

### 🧠 Complex Reasoning & Grounding
| Dataset | Primary Focus | Key Features | Target Optimization Challenges |
| :--- | :--- | :--- | :--- |
| **GaRAGe** | Grounding & Factuality | ~2.3K questions with human-annotated relevant/unknown passages; tests deflection (saying "I don't know"). | Low deflection rates leading to hallucinations; requires tuning retrieval confidence thresholds and refusal prompts. |
| **bLLeQA** | Legal Domain Grounding | Bilingual (French/Dutch) legal Q&A; tests refusal capabilities. | Domain terminology mismatch and refusal failures; requires cross-lingual retrieval calibration and legal entity linking. |
| **Legal RAG Bench** (`isaacus/legal-rag-bench`) | Reasoning-Intensive Legal RAG & Error Decomposition | Open-source benchmark decomposing errors into retrieval failures, reasoning failures, and hallucinations across legal corpora. | Retrieval failure masquerading as hallucinations; requires precision top-k retrieval, legal citation grounding, and hierarchical statute chunking. |
| **BrowseComp-Plus** | Deep Research Reasoning | 830 complex queries over ~100K web docs; labels evidence & gold docs. | Separating retriever recall from LLM reasoning; requires high-precision top-k filtering and multi-evidence re-ranking. |

### 🏢 Enterprise & Real-World Scenarios
| Dataset | Primary Focus | Key Features | Target Optimization Challenges |
| :--- | :--- | :--- | :--- |
| **EnterpriseRAG-Bench** | Enterprise Simulation | Simulates internal company data with >500K docs; includes low lexical overlap, multi-doc aggregation (up to 10 docs), and unanswerable queries. | Complex enterprise jargon, high corpus volume, and multi-document synthesis; requires hybrid indexing and multi-hop aggregation. |

### 💬 Conversational AI
| Dataset | Primary Focus | Key Features | Target Optimization Challenges |
| :--- | :--- | :--- | :--- |
| **mtRAG** | Multi-Turn Dialogue | 110 conversations (~7.7 turns each); features non-standalone questions and unanswerable queries. | Multi-turn conversational drift, pronoun resolution, and contextual query reformulation; requires query rewriting skills. |

---

## 6. Non-Goals (Out of Scope)

- Bundling a proprietary or hard-coded agent framework inside the CLI codebase (the CLI remains strictly deterministic and agent-neutral).
- Multimodal RAG pipelines (image/video/audio retrieval is out of scope; focus is purely on text-only RAG).
- Direct fine-tuning or weight training of generative LLMs (focus is purely on retrieval pipeline configuration and indexing optimization).
- Cloud-only SaaS infrastructure (CLI runs in local environments or developer-managed VMs with vLLM endpoints).

---

## 7. Design & Architecture Considerations

```
+--------------------------------------------------------------------+
|                         AI Agent (Hermes)                          |
|  Reads skills & reasons about failure modes -> executes CLI calls  |
+---------------------------------+----------------------------------+
                                  |
            [Terminal / Subprocess Tool Invocations]
                                  |
                                  v
+--------------------------------------------------------------------+
|                      Agent-Agnostic Plugin                         |
|  skills/chunking-tuning/SKILL.md                                   |
|  skills/query-rewriting/SKILL.md (mtRAG multi-turn)                |
|  skills/rerank-calibration/SKILL.md                                |
|  skills/embedding-model-selection/SKILL.md                         |
|  skills/deflection-and-refusal/SKILL.md (GaRAGe & bLLeQA)          |
|  skills/legal-retrieval-grounding/SKILL.md (Legal RAG Bench)       |
|  skills/multi-hop-aggregation/SKILL.md (EnterpriseRAG-Bench)       |
+---------------------------------+----------------------------------+
                                  |
                                  v
+--------------------------------------------------------------------+
|                     DeepFix Deterministic CLI                      |
|                                                                    |
|  deepfix dataset | deepfix index | deepfix eval                    |
|  deepfix run-diagnostic | deepfix mutate-config | deepfix export   |
+-------------------+----------------------------+-------------------+
                    |                            |
                    v                            v
          [pyTerrier / vLLM / Qwen]        [MLflow & DeepEval]
```

- **CLI Framework:** Built using `Typer` / `Click` with `Pydantic` for strict RAG config schema validation.
- **Output Standardization:** Standard exit codes (0 = success, 1 = validation error, 2 = runtime failure) and predictable JSON output structures for reliable LLM parsing.
- **Plugin Format:** Clean Markdown `SKILL.md` files compatible with multiple agent harnesses (Hermes, Claude, Antigravity).

---

## 8. Technical Considerations

- **Dependencies:** Python 3.10+, `pyterrier`, `deepeval`, `mlflow`, `typer`, `pydantic`, `rich`, `datasets` (Hugging Face).
- **Model Inference:** Inference endpoints (Qwen 3.6 27B, Qwen3-Embedding-4B) hosted via vLLM and referenced via environment variables / configuration URIs.
- **Dataset Storage & Streaming:** Local caching of benchmark datasets under `~/.deepfix/datasets/` with automated hash verification and chunked streaming loaders for massive corpora.
- **Benchmark Split:** Separation of dev dataset (used during autonomous agent loops) and held-out test dataset (locked for final benchmark verification).
- **Error Handling:** CLI must return informative error messages in JSON stderr so the agent can self-correct malformed arguments.

---

## 9. Success Metrics

- **Skill Coverage:** $\ge 5$ distinct retrieval and grounding failure remediation skills implemented and packaged in the plugin.
- **Autonomous Execution:** $\ge 80\%$ of multi-cycle optimization loops run by the Hermes agent complete without manual developer intervention.
- **Retrieval & Grounding Performance:** Autonomous optimization achieves:
  - **Faithfulness Score:** $\ge 0.80$ (DeepEval scale 0–1).
  - **Contextual Precision:** $\ge 0.75$.
  - **Answer Correctness:** $\ge 75\%$.
  - **Deflection / Refusal Accuracy:** $\ge 80\%$ on unanswerable query subsets (specifically on GaRAGe, bLLeQA, and EnterpriseRAG-Bench).
  - **Legal Retrieval Grounding:** $\ge 15\%$ reduction in retrieval-induced reasoning errors on **Legal RAG Bench**.
- **Benchmark Alignment:** Optimized RAG pipeline results within $10\%$ of the expert baseline on the held-out test sets across all benchmark domains.
- **Evaluation Weighting:** All benchmarks (including multilingual bLLeQA and jurisdictional Legal RAG Bench) are reported and evaluated independently as unweighted benchmark scores.

---

## 10. Resolved Architectural Decisions

- **Streaming Downloads for Enterprise Corpora:** **YES** — `deepfix dataset load --stream` supports memory-efficient chunked streaming for large-scale datasets (>500K documents like EnterpriseRAG-Bench).
- **Synthetic Deflection / Refusal Generators:** **YES** — `deepfix dataset generate-unanswerable` is built into the CLI to inject synthetic out-of-domain and unanswerable queries for evaluating deflection thresholds on custom user datasets.
- **Multilingual & Jurisdictional Metric Weighting:** **NO WEIGHTING** — Cross-lingual metrics in bLLeQA (French/Dutch) and jurisdictional legal metrics in Legal RAG Bench are evaluated directly without artificial weighting against general English benchmarks, ensuring transparent per-dataset scorecards.

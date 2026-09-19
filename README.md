# DeepFix: Autonomous RAG Optimization Framework
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Package Manager: uv](https://img.shields.io/badge/package%20manager-uv-green.svg)](https://github.com/astral-sh/uv)
[![Vector DB: LanceDB](https://img.shields.io/badge/Vector%20DB-LanceDB-orange.svg)](https://lancedb.com/)
[![Evaluation: DeepEval](https://img.shields.io/badge/Evaluation-DeepEval-purple.svg)](https://github.com/confident-ai/deepeval)
[![Tracking: MLflow](https://img.shields.io/badge/Tracking-MLflow-blue.svg)](https://mlflow.org/)
**DeepFix** is an agentic framework engineered for autonomous diagnosis and hyperparameter optimization of Retrieval-Augmented Generation (RAG) pipelines. It decouples deterministic RAG system operations from agentic reasoning:
1. **Deterministic Python CLI (`deepfix`)**: Scriptable, atomic subcommands (`dataset`, `index`, `eval`, `mutate-config`, `diff-config`, `run-diagnostic`, `export`) that execute pipeline tasks with structured JSON outputs and predictable exit codes.
2. **Agent-Agnostic Skill Plugin**: Modular failure-remediation skills (`SKILL.md`) that guide long-running AI agents (such as Hermes, Claude, or Antigravity) to identify root causes and run remediation cycles autonomously.
3. **MLflow Experiment Lineage**: Automatic tracking of config mutations, benchmark metrics, prompt artifacts, and evaluation deltas across optimization runs.
Developed by **Mittelstand Zukunftslab & AI Service Center (AISC) — HPI**.
---
## Architecture & Decoupled Workflow
DeepFix separates the optimization loop into two clear layers:
```
┌─────────────────────────────────────────────────────────────────────────┐
│                      AGENTIC LAYER (Hermes / LLM)                       │
│  - Reads modular SKILL.md guides (chunking, reranking, deflection, etc.) │
│  - Analyzes diagnostic failure categories & formulates hypotheses       │
│  - Issues deterministic CLI tool calls against `deepfix`                │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │  CLI Tool Calls (stdout / JSON)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DETERMINISTIC CLI TIER (`deepfix`)                  │
│                                                                         │
│   deepfix dataset load  ──▶  data/datasets/<benchmark>/<split>          │
│   deepfix index         ──▶  LanceDB Tables (Hybrid/Dense/Lexical)      │
│   deepfix eval          ──▶  DeepEval (Faithfulness, Precision, Refusal)│
│   deepfix mutate-config ──▶  Validated YAML configs (Pydantic Schema)   │
│   deepfix diff-config   ──▶  Parametric Delta Output                    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │  Traces, Metrics & Artifacts
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           MLflow Tracking                               │
│  - Benchmark metrics (Contextual Precision, Faithfulness, Correctness)  │
│  - Configuration history, prompt templates, and run lineage             │
└─────────────────────────────────────────────────────────────────────────┘
```
---
## Feature Matrix & Implementation Status
| Feature / Subcommand | Status | Description |
| :--- | :---: | :--- |
| `deepfix dataset load` | `[Ready]` | Ingests benchmark datasets with strict `dev`/`test` split isolation and HuggingFace streaming. |
| `deepfix index` | `[Ready]` | Builds vector & lexical tables in LanceDB with Octen-8B / Qwen embeddings. |
| `deepfix eval` | `[Ready]` | Runs DeepEval scoring; supports `--compare` for automatic delta metrics. |
| `deepfix mutate-config` | `[Ready]` | Validates dot-notation parameter overrides against Pydantic schema and outputs updated YAML. |
| `deepfix diff-config` | `[Ready]` | Computes structural and hyperparameter diffs between two YAML configurations. |
| `deepfix run-diagnostic` | `[In Progress]` | Categorizes failure modes (retrieval vs. ranking vs. grounding vs. entity). |
| `deepfix export` | `[In Progress]` | Packages best configuration with evaluation lineage into standalone deployment bundles. |
| **Autonomous Agent Loop** | `[In Progress]` | Multi-cycle closed-loop optimization executed by Hermes via `SKILL.md` plugins. |
---
## Supported Benchmark Matrix
DeepFix targets complex, domain-specific, and large-scale RAG benchmarks:
| Benchmark | Domain / Task | Failure Focus | Split Isolation | Source / Repository |
| :--- | :--- | :--- | :---: | :--- |
| **GaRAGe** | General Question Answering | Grounding, factuality & unanswerable deflection ("I don't know") | `dev` / `test` | `ds-ba/GaRAGe` |
| **bLLeQA** | Multilingual (FR/NL) Legal QA | Cross-lingual grounding and statutory refusal calibration | `dev` / `test` | `ds-ba/bLLeQA` |
| **Legal RAG Bench** | Complex Statutory Analysis | Decomposes retrieval misses vs. reasoning failures vs. hallucinations | `dev` / `test` | `isaacus/legal-rag-bench` |
| **BrowseComp-Plus** | Web Deep Research (~100k docs) | Multi-hop reasoning and long-tail document retrieval | `dev` / `test` | `ds-ba/BrowseComp-Plus` |
| **EnterpriseRAG-Bench**| Enterprise Knowledge (>500k docs)| Low lexical overlap, enterprise jargon & multi-doc aggregation | `dev` / `test` | `ds-ba/EnterpriseRAG-Bench` |
| **mtRAG** | Multi-Turn Conversational AI | Context maintenance across turns and topic shifts | `dev` / `test` | `ds-ba/mtRAG` |
> [!IMPORTANT]
> **Split Isolation Policy**: Optimization and tuning loops **only** execute against the `dev` split. The `test` split is strictly locked for final evaluation.
---
## Installation & Prerequisites
### Prerequisites
- **Python**: `~=3.12.0`
- **Package Manager**: [uv](https://github.com/astral-sh/uv) (recommended) or standard `pip`
### Setup
```bash
# 1. Clone repository
git clone https://github.com/hpi-dhc/pilotproject-deepfix.git
cd pilotproject-deepfix
# 2. Create environment & install dependencies with uv
uv sync
# 3. Activate the virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate
```
Alternatively, use the provided `Makefile`:
```bash
make create_environment
make requirements
```
---
## Environment Configuration (`.env`)
DeepFix uses a `.env` file in the project root to configure inference model backends and MLflow tracking.
Create `.env` using the template below:
```dotenv
# ==============================================================================
# 1. Model Inference Endpoints (AISC / Octen / LiteLLM)
# ==============================================================================
# HPI AI Service Center (AISC) / Octen Inference
AISC_URL=https://api.aisc.hpi.de/v1
AISC_KEY=your_aisc_api_key_here
# LiteLLM Proxy / OpenRouter (optional fallback)
LITELLM_URL=https://litellm.mittelstand-zukunftslab.de
LITELLM_KEY=your_litellm_api_key_here
OPENROUTER_KEY=your_openrouter_api_key_here
# Provider selector: 'aisc', 'octen', 'hpi', or 'openrouter'
DEEPFIX_INFERENCE_PROVIDER=aisc
# ==============================================================================
# 2. MLflow Experiment Tracking
# ==============================================================================
MLFLOW_TRACKING_URI=https://mlflow.mittelstand-zukunftslab.de
MLFLOW_TRACKING_USERNAME=your_username
MLFLOW_TRACKING_PASSWORD=your_password
MLFLOW_DEV_WORKSPACE=deepfix-dev
MLFLOW_EXPERIMENT_NAME=deepfix-rag-optimization
# ==============================================================================
# 3. Local LanceDB & Data Storage
# ==============================================================================
LANCEDB_DATA_DIR=data/lancedb
DATASET_CACHE_DIR=data/datasets
```
---
## Quickstart: Step-by-Step Optimization Walkthrough
Here is an end-to-end scenario demonstrating how DeepFix loads data, indexes passages, computes baselines, mutates parameters, and compares improvements.
### 1. Ingest Benchmark Dataset
Fetch the development split of the benchmark dataset (e.g. `garage`):
```bash
deepfix dataset load --name garage --split dev
```
Files are saved to `data/datasets/garage/dev/corpus.jsonl` and `qa.jsonl`.
### 2. Build LanceDB Index
Generate embeddings and build the LanceDB hybrid table:
```bash
deepfix index \
  --dataset data/datasets/garage/dev/corpus.jsonl \
  --config configs/default.yaml \
  --index-dir data/indexes/garage_dev \
  --force
```
### 3. Run Baseline Evaluation
Evaluate the baseline configuration using DeepEval metrics:
```bash
deepfix eval \
  --dataset data/datasets/garage/dev/qa.jsonl \
  --config configs/default.yaml \
  --index-dir data/indexes/garage_dev \
  --limit 20
```
### 4. Mutate Hyperparameters
Create a new candidate configuration by overriding parameters (e.g., increasing `retrieval.top_k` and tuning `retrieval.dense_weight`):
```bash
deepfix mutate-config \
  --base configs/default.yaml \
  --set retrieval.top_k=20 \
  --set retrieval.dense_weight=0.7 \
  --output configs/candidate_v1.yaml
```
Inspect the parameter diff:
```bash
deepfix diff-config configs/default.yaml configs/candidate_v1.yaml
```
### 5. Evaluate Candidate & Compare Delta
Run evaluation with `--compare` to get side-by-side metric deltas:
```bash
deepfix eval \
  --dataset data/datasets/garage/dev/qa.jsonl \
  --config configs/candidate_v1.yaml \
  --compare configs/default.yaml \
  --index-dir data/indexes/garage_dev \
  --limit 20
```
Output includes `candidate`, `baseline`, and `delta` for:
- `faithfulness_avg`
- `contextual_precision_avg`
- `answer_correctness_avg`
- `deflection_rate`
### 6. Register Prompts in MLflow
Register system and query prompts to MLflow:
```bash
python register_prompt.py
```
---
## CLI Command Reference
All subcommands support `--json` for headless automation by LLM agents.
### `deepfix dataset load`
Download and format benchmark datasets into canonical JSON Lines format.
```bash
deepfix dataset load --name <preset_or_hf_repo> --split <dev|test> [--stream]
```
### `deepfix index`
Build dense, sparse, or hybrid LanceDB indexes from a corpus.
```bash
deepfix index --dataset <path_to_corpus> --config <config.yaml> --index-dir <dir> [--force] [--json]
```
### `deepfix eval`
Run DeepEval benchmark evaluation on a QA dataset.
```bash
deepfix eval --dataset <path_to_qa> --config <config.yaml> [--index-dir <dir>] [--compare <base.yaml>] [--limit <N>] [--json]
```
### `deepfix mutate-config`
Validate and mutate RAG pipeline hyperparameters using dot-notation keys.
```bash
deepfix mutate-config --base <base.yaml> --set <key.subkey=value> --output <new_config.yaml> [--json]
```
### `deepfix diff-config`
Structural diff between two RAG YAML configurations.
```bash
deepfix diff-config <config_a.yaml> <config_b.yaml> [--json]
```
### `deepfix run-diagnostic` `[In Progress]`
Classify low-scoring queries into specific failure modes.
```bash
deepfix run-diagnostic --dataset <path> --type <retrieval|entity|ranking|grounding> [--threshold 0.7] [--json]
```
### `deepfix export` `[In Progress]`
Export the best candidate configuration bundle and metadata.
```bash
deepfix export --config <best_config.yaml> --output <dist_path> [--include-meta] [--json]
```
---
## Configuration Schema
Configurations are validated against Pydantic models in `src/deepfix_rag/config/schema.py`:
```yaml
version: "1.0"
chunking:
  strategy: recursive        # recursive | fixed | semantic
  size: 512                 # 64 to 4096
  overlap: 64
embedding:
  model: octen-embedding-8b # octen-embedding-8b | Qwen/Qwen3-Embedding-4B
  provider: aisc            # aisc | octen | hpi | openrouter
  batch_size: 32
retrieval:
  backend: hybrid           # hybrid | dense | pyterrier | bm25
  top_k: 10                 # 1 to 100
  dense_weight: 0.5         # 0.0 to 1.0 (dense component weight)
  bm25_weight: 0.5          # 0.0 to 1.0 (lexical component weight)
rerank:
  enabled: false
  model: rerank-english-v3.0
  top_n: 5
generation:
  model: Qwen/Qwen2.5-32B-Instruct
  provider: hpi
  temperature: 0.0
  max_tokens: 1024
```
---
## Autonomous Optimization Workflow (Hermes Agent)
In autonomous mode, an LLM agent executes multi-cycle optimization loops guided by the DeepFix skill plugin:
1. **Observe**: Agent runs `deepfix run-diagnostic` and `deepfix eval` on the `dev` split.
2. **Match Skill**: Agent matches identified failures to modular skill instructions:
   - `skills/chunking-tuning/SKILL.md` (addressing context fragmentation)
   - `skills/rerank-calibration/SKILL.md` (calibrating cross-encoder thresholds)
   - `skills/query-rewriting/SKILL.md` (resolving vocabulary mismatch and multi-turn context)
   - `skills/deflection-and-refusal/SKILL.md` (tuning confidence cutoffs for unanswerable queries)
   - `skills/embedding-model-selection/SKILL.md` (dense vs. hybrid trade-offs)
3. **Act**: Agent mutates configuration (`deepfix mutate-config`), rebuilds index (`deepfix index` if needed), and re-evaluates (`deepfix eval --compare`).
4. **Decide**: If metrics improve, the mutation is adopted as the new baseline; otherwise, it is reverted.
5. **Log**: All runs, parameters, and evaluation deltas are logged to MLflow.
---
## Project Organization
```
pilotproject-deepfix/
├── configs/                       <- RAG configuration YAML templates
│   ├── default.yaml               <- Default hybrid baseline
│   ├── octen.yaml                 <- Octen-8B embedding configuration
│   └── rerank.yaml                <- Re-ranking configuration
├── data/                          <- Benchmark data and local indexes (ignored by git)
│   ├── datasets/                  <- Ingested benchmark splits (corpus.jsonl, qa.jsonl)
│   └── indexes/                   <- LanceDB table indexes
├── docs/                          <- PRD, architecture notes, and UX flows
│   ├── prd-deepfix-cli.md         <- Comprehensive Product Requirements Document
│   └── plan-deepfix-ux-flow.md    <- UX flow plan and architectural boundary specs
├── models/                        <- Local serialized models / cache
├── notebooks/                     <- Exploratory Jupyter notebooks
├── src/deepfix_rag/               <- Main Python package source code
│   ├── cli/                       <- CLI subcommands (Typer)
│   │   ├── config.py              <- mutate-config and diff-config
│   │   ├── dataset.py             <- dataset load and split management
│   │   ├── diagnostic.py          <- run-diagnostic (failure analysis)
│   │   ├── eval.py                <- eval with DeepEval and delta comparisons
│   │   ├── export.py              <- export configuration bundles
│   │   ├── index.py               <- index building with LanceDB
│   │   └── ui.py                  <- Rich console formatting and JSON output
│   ├── config/                    <- Configuration schema and mutator logic
│   │   ├── mutator.py             <- Parameter override and diff engine
│   │   └── schema.py              <- Pydantic RAGConfig model definition
│   ├── diagnostics/               <- Failure mode classification engine
│   ├── evaluation/                <- Evaluation runner and MLflow integrations
│   │   └── runner.py              <- DeepEval pipeline execution runner
│   ├── retrieval/                 <- Retrieval and generation engines
│   │   ├── models.py              <- EmbeddingClient and GenerationClient
│   │   └── pipeline.py            <- Hybrid and dense retrieval pipeline
│   └── main.py                    <- Entrypoint CLI application (`deepfix`)
├── tests/                         <- Test suite (pytest + conftest.py)
│   ├── conftest.py                <- Shared fixtures (cli_runner, configs, benchmark paths)
│   ├── data/                      <- Test benchmark datasets
│   │   ├── sample_corpus.jsonl    <- Sample German legal corpus for tests
│   │   └── sample_qa.jsonl        <- Sample German legal QA pairs
│   ├── test_cli.py                <- CLI subcommands, JSON schema & exit codes
│   ├── test_config_mutator.py     <- Config schema, parameter mutations & diffs
│   ├── test_dataset_cli.py        <- Dataset CLI commands and split isolation
│   ├── test_dataset_loaders.py    <- Hugging Face / local dataset loader routing
│   ├── test_end_to_end.py         <- Full ingestion, LanceDB indexing & hybrid retrieval
│   └── test_index_creation.py     <- LanceDB index building & overwrite safety
├── candidate_manifest.json        <- Example MLflow candidate artifact
├── Makefile                       <- Development and environment convenience tasks
├── pyproject.toml                 <- Package dependencies, Ruff & pytest configuration
├── register_prompt.py             <- Script to register RAG prompts with MLflow
└── README.md                      <- Project documentation (this file)
```
---
## Development & Testing

DeepFix uses [pytest](https://docs.pytest.org/) for automated testing and [Ruff](https://docs.astral.sh/ruff/) for code formatting and linting.

### Running Tests

Run the full test suite with `uv`:
```bash
uv run --env-file .env pytest -v
```
Alternatively, with an active virtual environment:
```bash
pytest
# or via Makefile
make test
```

### Pytest Markers & Selective Execution

Tests are categorized using markers registered in `pyproject.toml` to allow targeted execution:

| Marker | Description | Example Target |
| :--- | :--- | :--- |
| `unit` | Fast unit tests with no network or external dependencies | Schema validation, mutators, loaders |
| `cli` | Typer CLI commands, JSON contract & exit code validation | `test_cli.py`, `test_dataset_cli.py` |
| `integration` | Tests requiring local filesystem, LanceDB tables, or loaders | `test_index_creation.py`, local loader tests |
| `e2e` | Full end-to-end pipeline ingestion, embedding & retrieval | `test_end_to_end.py` |
| `network` | Tests interacting with live Hugging Face Hub APIs | Live dataset download tests |

#### Run Fast Unit Tests Only
```bash
uv run --env-file .env pytest -v -m "unit"
```

#### Run CLI Tests Only
```bash
uv run --env-file .env pytest -v -m "cli"
```

#### Run Fast Tests (Skip Network & E2E)
```bash
uv run --env-file .env pytest -v -m "not network and not e2e"
```

#### Run End-to-End Pipeline Tests
```bash
uv run --env-file .env pytest -v -m "e2e"
```

### Shared Fixtures (`tests/conftest.py`)

Common fixtures are defined in [`tests/conftest.py`](tests/conftest.py) for reuse across test modules:
- **`cli_runner`**: Provides an isolated `typer.testing.CliRunner` instance.
- **`default_config_path`**: Path to root [`configs/default.yaml`](configs/default.yaml).
- **`sample_corpus_path`**: Path to [`tests/data/sample_corpus.jsonl`](tests/data/sample_corpus.jsonl).
- **`sample_qa_path`**: Path to [`tests/data/sample_qa.jsonl`](tests/data/sample_qa.jsonl).
- **`default_rag_config`**: Fresh default `RAGConfig` instance.
- **`sample_rag_config`**: Pre-configured `RAGConfig` referencing sample benchmark fixtures.

### Code Quality & Linting
```bash
# Check code style and imports with ruff
uv run ruff check

# Automatically format and fix issues
uv run ruff check --fix
uv run ruff format
```
---
## License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

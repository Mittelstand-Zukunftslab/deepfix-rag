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
| `deepfix dataset load` | `[Ready]` | Ingests benchmark datasets (Bundestag, Legal RAG Bench, local files) into standardized JSON Lines format. |
| `deepfix index` | `[Ready]` | Builds vector & lexical tables in LanceDB with Octen-8B / Qwen embeddings. |
| `deepfix eval` | `[Ready]` | Runs DeepEval scoring; supports `--compare` for automatic delta metrics. |
| `deepfix mutate-config` | `[Ready]` | Validates dot-notation parameter overrides against Pydantic schema and outputs updated YAML. |
| `deepfix diff-config` | `[Ready]` | Computes structural and hyperparameter diffs between two YAML configurations. |
| `deepfix hpo` | `[Ready]` | Executes multi-paradigm hyperparameter optimization (dense, hybrid, graph) using Optuna. |
| `deepfix run-diagnostic` | `[In Progress]` | Categorizes failure modes (retrieval vs. ranking vs. grounding vs. entity). |
| `deepfix export` | `[In Progress]` | Packages best configuration with evaluation lineage into standalone deployment bundles. |
| **Autonomous Agent Loop** | `[In Progress]` | Multi-cycle closed-loop optimization executed by Hermes via `SKILL.md` plugins. |
---
## Supported Benchmark Matrix
DeepFix targets complex, domain-specific legal and parliamentary RAG benchmarks implemented in `src/deepfix_rag/data.py`:
| Benchmark | Preset / Alias | Domain / Task | Failure Focus | Split / Subsets | Source / Repository |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Bundestag Parliamentary Benchmark** | `bundestag-qa`<br>`bundestag-corpus` | German parliamentary debates & legislative inquiry QA (DIP) | Domain terminology, multi-document aggregation, legislative context | `train` | `wirthual/dip-bundestag` (corpus)<br>`wirthual/dip-bundestag-qa` (QA) |
| **Legal RAG Bench** | `legal-rag-bench` | Complex statutory analysis & legal QA (US statutes, regulatory code, footnotes) | Decomposes retrieval misses vs. reasoning failures vs. hallucinations | `test`<br>(`corpus` / `qa`) | `isaacus/legal-rag-bench` |
| **Custom / Local Benchmarks** | `<path.jsonl>`<br>`<dir_path>` | Arbitrary domain corpora and evaluation QA pairs | Custom evaluation & domain-specific failure mode diagnostics | Flexible | Local JSON / JSONL files or directory (`LocalFileLoader`) |
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

### Install the DeepFix Diagnostic Server

`deepfix run-diagnostic` delegates agentic failure analysis to the separate [DeepFix diagnostic server](https://github.com/delcaux-labs/deepfix/tree/main/packages/deepfix-server). Install its `deepfix-server` command in an isolated environment with [`uv tool install`](https://docs.astral.sh/uv/guides/tools/#installing-tools):

```bash
uv tool install "git+https://github.com/delcaux-labs/deepfix.git#subdirectory=packages/deepfix-server"
deepfix-server version
```

If the executable is not available in a new terminal, add uv's tool directory to your shell's `PATH`, then restart the terminal:

```bash
uv tool update-shell
```

Configure the server's model providers before launching it. The server reads these variables from the process environment or from a `.env` file in the directory where it is launched. The server CLI does not currently accept an env-file option, so either launch it from the directory containing that `.env` file or export the variables in the shell first:

```dotenv
DEEPFIX_LLM_API_KEY=
DEEPFIX_LLM_BASE_URL=https://openrouter.ai/api/v1
DEEPFIX_LLM_MODEL_NAME=qwen/qwen3.8-flash
DEEPFIX_LLM_TEMPERATURE=0.7
DEEPFIX_LLM_MAX_TOKENS=8000
DEEPFIX_LLM_CACHE=1
DEEPFIX_LLM_TRACK_USAGE=1

DEEPFIX_SEARCH_PROVIDER="duckduckgo"

NUM_REASONING_CHAINS=1

DEEPFIX_SERVER_DATABASE_URL=sqlite:///./deepfix_server.db

MLFLOW_TRACKING_URI=
MLFLOW_EXP_NAME=Deepfix-server
```

```bash
deepfix-server launch -host 0.0.0.0 -port 8844
```

For reproducible deployments, pin the installation to a release tag or commit:

```bash
uv tool install "git+https://github.com/delcaux-labs/deepfix.git#subdirectory=packages/deepfix-server"
```

Manage the installed tool with:

```bash
uv tool upgrade deepfix-server
uv tool uninstall deepfix-server
```
---
## Environment Configuration (`.env`)
DeepFix uses a `.env` file in the project root for MLflow tracking, the diagnostic server, model endpoints, S3 storage, graph databases, and the Hermes agent.
Copy the template and fill in your credentials:
```bash
cp example.env .env
```
```dotenv
# ------------------------------------------------------------------------------
# MLflow Tracking & Experiment Configuration
# ------------------------------------------------------------------------------
MLFLOW_TRACKING_URI=https://mlflow.mittelstand-zukunftslab.de
MLFLOW_DEV_RAG_EXPERIMENT=dev_rag_experiment
MLFLOW_ENABLE_ASYNC_TRACE_LOGGING=true

DEEPFIX_SERVER_URL=https://deepfix-server.mittelstand-zukunftslab.de
DEEPFIX_API_KEY=

# ------------------------------------------------------------------------------
# LLM Inference Configuration
# ------------------------------------------------------------------------------
LLM_BASE_URL=https://api.aisc.hpi.de/v1
LLM_API_KEY=
LLM_MODEL=qwen3-8-27b
LLM_JUDGE_MODEL=gpt-oss-120b

# S3-compatible storage
S3_ENDPOINT_URL=https://hpi-s3.mittelstand-zukunftslab.de
S3_URI=s3://deepfix-index/dev
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=

HF_TOKEN=

# ------------------------------------------------------------------------------
# Embedding Model Configuration
# ------------------------------------------------------------------------------
EMBEDDING_BASE_URL=https://api.aisc.hpi.de/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=octen-embedding-8b

# ------------------------------------------------------------------------------
# Reranker Configuration (Cohere-compatible API)
# ------------------------------------------------------------------------------
COHERE_BASE_URL=https://api.aisc.hpi.de
COHERE_API_KEY=
COHERE_MODEL=qwen3-reranker-4b

# ------------------------------------------------------------------------------
# Memgraph Graph Database
# ------------------------------------------------------------------------------
MEMGRAPH_URL=bolt://localhost:7687
MEMGRAPH_USERNAME=neo4j
MEMGRAPH_PASSWORD=neo4j

# ------------------------------------------------------------------------------
# FalkorDB Graph Database
# ------------------------------------------------------------------------------
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_USERNAME=
FALKORDB_PASSWORD=
FALKORDB_DATABASE=my_knowledge_graph

# ------------------------------------------------------------------------------
# Hermes Agent
# ------------------------------------------------------------------------------
HERMES_DASHBOARD=
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=
HERMES_DASHBOARD_BASIC_AUTH_SECRET=
API_SERVER_ENABLED=
API_SERVER_HOST=
API_SERVER_KEY=

# ------------------------------------------------------------------------------
# MLflow Server (docker compose)
# The tracking client above still uses MLFLOW_TRACKING_URI.
# Point that URI at http://localhost:5000 to use this local server.
# ------------------------------------------------------------------------------
MLFLOW_BACKEND_STORE_URI=sqlite:////opt/mlflow/data/mlflow.db
MLFLOW_AUTH_CONFIG_PATH=/opt/mlflow/data/basic_auth.ini
MLFLOW_FLASK_SERVER_SECRET_KEY=change-me
MLFLOW_ADMIN_PASSWORD=change-me
MLFLOW_ALLOWED_HOSTS=localhost,127.0.0.1
MLFLOW_CORS_ALLOWED_ORIGINS=http://localhost:5000
```
---
## Quickstart: Step-by-Step Optimization Walkthrough
Here is an end-to-end scenario demonstrating how DeepFix loads data, indexes passages, computes baselines, mutates parameters, and compares improvements.
### 1. Ingest Benchmark Dataset
Fetch the benchmark dataset (e.g. `bundestag-qa`):
```bash
deepfix dataset load --name bundestag-qa --split train
```
Files are saved to `data/datasets/bundestag-qa/train/corpus.jsonl` and `qa.jsonl`.
### 2. Build LanceDB Index
Generate embeddings and build the LanceDB hybrid table:
```bash
deepfix index \
  --dataset data/datasets/bundestag-qa/train/corpus.jsonl \
  --config configs/default.yaml \
  --index-dir data/indexes/bundestag_train \
  --force
```
### 3. Run Baseline Evaluation
Evaluate the baseline configuration using DeepEval metrics:
```bash
deepfix eval \
  --dataset data/datasets/bundestag-qa/train/qa.jsonl \
  --config configs/default.yaml \
  --index-dir data/indexes/bundestag_train \
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
  --dataset data/datasets/bundestag-qa/train/qa.jsonl \
  --config configs/candidate_v1.yaml \
  --compare configs/default.yaml \
  --index-dir data/indexes/bundestag_train \
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
deepfix dataset load --name <preset_or_hf_repo> [--split train] [--limit <N>] [--subset <corpus|qa>] [--corpus-only] [--data-root <dir>] [--json]
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
### `deepfix hpo`
Execute multi-paradigm hyperparameter optimization study using Optuna.
```bash
deepfix hpo --config <hpo_config.yaml> [--n-trials <N>] [--storage-uri <uri>] [--paradigm <dense|hybrid|graph>] [--output <best.yaml>] [--json]
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
│   │   ├── dataset.py             <- dataset load, list, and unanswerable generation
│   │   ├── diagnostic.py          <- run-diagnostic (failure analysis)
│   │   ├── eval.py                <- eval with DeepEval and delta comparisons
│   │   ├── export.py              <- export configuration bundles
│   │   ├── hpo.py                 <- multi-paradigm Optuna HPO study CLI
│   │   ├── index.py               <- index building with LanceDB
│   │   └── ui.py                  <- Rich console formatting and JSON output
│   ├── config/                    <- Configuration schema and mutator logic
│   │   ├── hpo.py                 <- Optuna HPO search space and study config
│   │   ├── mutator.py             <- Parameter override and diff engine
│   │   ├── schema.py              <- Pydantic RAGConfig and DataLoaderConfig schemas
│   │   └── settings.py            <- Environment settings and logger setup
│   ├── data.py                    <- Benchmark loader strategies and DataLoader facade
│   ├── diagnostics/               <- Failure mode classification engine
│   ├── evaluation/                <- Evaluation runner and MLflow integrations
│   │   └── runner.py              <- DeepEval pipeline execution runner
│   ├── hpo.py                     <- HyperparameterOpt execution engine
│   ├── pipeline.py                <- LlamaIndex Workflow RAG pipeline (hybrid/dense/BM25)
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

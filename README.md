# DeepFix: Autonomous RAG Optimization Framework
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Package Manager: uv](https://img.shields.io/badge/package%20manager-uv-green.svg)](https://github.com/astral-sh/uv)
[![Vector DB: LanceDB](https://img.shields.io/badge/Vector%20DB-LanceDB-orange.svg)](https://lancedb.com/)
[![Evaluation: DeepEval](https://img.shields.io/badge/Evaluation-DeepEval-purple.svg)](https://github.com/confident-ai/deepeval)
[![Tracking: MLflow](https://img.shields.io/badge/Tracking-MLflow-blue.svg)](https://mlflow.org/)
**DeepFix** is an agentic framework engineered for autonomous diagnosis and hyperparameter optimization of Retrieval-Augmented Generation (RAG) pipelines. It decouples deterministic RAG system operations from agentic reasoning:
1. **Deterministic Python CLI (`deepfix`)**: Scriptable, atomic subcommands (`dataset`, `index`, `eval`, `gate`, `mutate-config`, `diff-config`, `schema`, `run-diagnostic`, `export`) that execute pipeline tasks with structured JSON outputs and predictable exit codes.
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
| `deepfix eval` | `[Ready]` | Runs DeepEval scoring; supports `--compare` for automatic delta metrics and `--no-mlflow` for offline runs. |
| `deepfix gate` | `[Ready]` | Decides keep or revert from paired per-query compare results and a seeded bootstrap interval. |
| `deepfix schema` | `[Ready]` | Prints the RAGConfig schema, including the index-time keys. |
| `deepfix mutate-config` | `[Ready]` | Validates dot-notation parameter overrides against Pydantic schema and outputs updated YAML. Unknown keys exit 1. |
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
git clone https://github.com/Mittelstand-Zukunftslab/deepfix-rag.git
cd deepfix-rag
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
# LiteLLM reads this flag. Claude Haiku 5.5 allows only temperature=1.
LITELLM_DROP_PARAMS=1

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

### Run MLflow Tracking Server Locally (`uvx`)

DeepFix tracks evaluation metrics, run configurations, prompt artifacts, and comparison deltas in MLflow. If you want to run a local MLflow tracking server without Docker or installing MLflow into your virtual environment, run it on-demand with [`uvx`](https://docs.astral.sh/uv/guides/tools/#running-tools):

```bash
uvx mlflow server --host 127.0.0.1 --port 5000
```

To persist runs and experiment artifacts in a local SQLite database across restarts:

```bash
uvx mlflow server \
  --backend-store-uri sqlite:///mlflow.db \
  --default-artifact-root ./mlruns \
  --host 127.0.0.1 \
  --port 5000
```

Once the server is running, the MLflow dashboard is available at `http://127.0.0.1:5000`. Point your tracking URI in `.env` to the local instance:

```dotenv
MLFLOW_TRACKING_URI=http://127.0.0.1:5000
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
# Answer generation model, served by LLM_BASE_URL.
LLM_MODEL=qwen3-8-27b
# DeepEval judge. It may differ from LLM_MODEL. This deployment points
# LLM_JUDGE_MODEL at the same endpoint as the answers.
LLM_JUDGE_MODEL=gpt-oss-120b
# Drop sampling params the provider rejects (Claude Haiku 5.5 allows only temperature=1).
DEEPFIX_LLM_DROP_PARAMS=1

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
# Reranker Configuration (OpenRouter rerank API)
# Rerank posts to POST <COHERE_BASE_URL>/rerank with OPENROUTER_API_KEY.
# ------------------------------------------------------------------------------
COHERE_BASE_URL=https://openrouter.ai/api/v1
COHERE_MODEL=cohere/rerank-4-fast

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
# Hermes Agent (internal sandbox; no published port, dashboard off)
# ------------------------------------------------------------------------------
# Required. At least 16 characters. Generate with: openssl rand -hex 32
API_SERVER_KEY=
# Distinct from LLM_API_KEY. Not sent to the Hermes container as LLM_API_KEY.
OPENROUTER_API_KEY=
# Model the Hermes sandbox optimization agent runs. Required: the sandbox
# writes it into /opt/data/config.yaml and the API posts it on POST /v1/runs.
# Both services read the same value, so they cannot drift.
HERMES_MODEL=deepseek/deepseek-v4.1-flash-1017
# Optional chat-profile key. The chat profile is not started.
HERMES_CHAT_API_SERVER_KEY=

# ------------------------------------------------------------------------------
# MLflow Server (docker compose)
# The tracking client above still uses MLFLOW_TRACKING_URI.
# Point that URI at http://localhost:5000 to use this local server.
MLFLOW_ALLOWED_HOSTS=localhost,127.0.0.1
MLFLOW_CORS_ALLOWED_ORIGINS=http://localhost:5000
# Optional overrides. MLflow 3.x matches Host exactly (include hostname:port).
MLFLOW_SERVER_ALLOWED_HOSTS=hermes,openhands,localhost,diagnostic-agent,mlflow,mlflow:5000,localhost:5000,127.0.0.1,127.0.0.1:5000,backend-api,deepfix-mlflow.mittelstand-zukunftslab.de
MLFLOW_SERVER_CORS_ALLOWED_ORIGINS=http://hermes,http://openhands,http://localhost,http://diagnostic-agent,http://mlflow:5000,http://backend-api:8000,https://deepfix-mlflow.mittelstand-zukunftslab.de
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
Output includes `candidate`, `baseline`, and `delta`. Metric keys are unsuffixed names such as `faithfulness`, `context_precision`, `context_recall`, `answer_relevancy`, `contextual_relevancy`, `ndcg`, `recall`, `precision`, and `deflection_rate`. Compare runs also include `effective_backend`, `configured_backend`, `judge_failures`, and per-query `questions`. Decide keep or revert with:

```bash
deepfix gate --compare compare.json --primary ndcg --json
```
### 6. Register Prompts in MLflow
Register system and query prompts to MLflow:
```bash
python scripts/register_prompt.py
```
---
## CLI Command Reference
All subcommands support `--json` for headless automation by LLM agents. `deepfix export` is not implemented and always exits 2.
### `deepfix dataset load`
Download and format benchmark datasets into canonical JSON Lines format.
```bash
deepfix dataset load --name <preset_or_hf_repo> [--split train] [--limit <N>] [--subset <corpus|qa>] [--corpus-only] [--data-root <dir>] [--json]
```
### `deepfix index`
Build dense, sparse, or hybrid LanceDB indexes from a corpus.
```bash
deepfix index --dataset <path_to_corpus> --config <config.yaml> --index-dir <dir> [--force] [--if-changed] [--json]
```
### `deepfix eval`
Run DeepEval benchmark evaluation on a QA dataset.
```bash
deepfix eval --dataset <path_to_qa> --config <config.yaml> [--index-dir <dir>] [--baseline-index-dir <dir>] [--compare <base.yaml>] [--limit <N>] [--seed <N>] [--no-mlflow] [--json]
```

`--index-dir` moves only the candidate. `--baseline-index-dir` moves the `--compare` baseline, so a round can score both sides from one local copy of a remote index (`<run_dir>/idx-cache`) instead of re-reading the object store.
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
### `deepfix gate`
Decide keep or revert from an `eval --compare` JSON file. The threshold is `OPTIMIZATION_MIN_PRIMARY_DELTA` (default 0.025), not a hardcoded constant.
```bash
deepfix gate --compare <compare.json> --primary <ndcg|recall|precision> [--json]
```
### `deepfix schema`
Print fields, bounds, enums, and the index-time key list.
```bash
deepfix schema --json
```
### `deepfix run-diagnostic` `[In Progress]`
Classify low-scoring queries into specific failure modes. There is no `--type` or `--threshold` flag.
```bash
deepfix run-diagnostic --dataset <path> --config <config.yaml> [--server-url <url>] [--limit <N>] [--json]
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
deepfix hpo --config configs/hpo_retrieval_small.yaml --retrieval-only --limit 20 --seed 0 \
  --n-trials 12 --timeout 2400 --storage-uri sqlite:///<run_dir>/hpo.db \
  --output <run_dir>/candidate.yaml --run-dir <run_dir> --json
```

`--seed` draws a deterministic sample of `--limit` rows for every trial (the same semantics as `deepfix eval --seed`). `--timeout` caps the study wall clock. `--run-dir` keeps `hpo_trials.json` and the best-so-far YAML fresh after every trial, so a timeout or a crash still leaves a usable candidate. When the base config names an `s3://` index, HPO copies the whole prefix — bucket and key — once into `<run_dir>/idx-cache` (or the `--index-dir` you pass) and every trial reads that local copy. Point the confirmation evals at the same cache with `--index-dir` and `--baseline-index-dir`.

The exported candidate contains the base config plus the tuned parameters only. `data.limit`, `data.path_or_repo` and `retrieval.lancedb_index_dir` are trial-only overrides and never appear in it, so the candidate is re-scored on the full dev set against the champion's own dataset and index.

A trial whose parameter set duplicates an earlier one is pruned without running an eval: it costs nothing and cannot become the best trial. `n_trials` counts evaluated trials, so pruning never shortens the budget; `pruned_trials` reports the overhead.

A trial where every case errored is recorded `FAIL` rather than scored `0.0`, so a broken lever cannot look like a worse one. If no trial completes, the command exits 2 with the first trial's error.

`configs/hpo_retrieval_small.yaml` is the shipped query-time space: rerank on/off with `top_n` 5 or 10, `top_k` 10/20/50, and the three fusion modes that read their weights. It excludes `bm25_k1`, `bm25_b`, query expansion above 1, and every chunking key. `.agents/skills/hpo-search/SKILL.md` is the procedure a Hermes round follows with it.
---
## Configuration Schema
Configurations are validated against Pydantic models in `src/deepfix_rag/config/schema.py`:
```yaml
version: "1.0"
chunking:
  strategy: sentence        # sentence | recursive | fixed | semantic | hierarchical
  size: 1024                # 64 to 16000
  overlap: 64               # must be < size for sentence and fixed
embedding:
  batch_size: 32            # the model id is EMBEDDING_MODEL in the environment, not a config key
retrieval:
  backend: hybrid           # hybrid | dense | bm25 | graph | mixed
  top_k: 10                 # 1 to 100
  fusion_mode: reciprocal_rerank  # reciprocal_rerank | relative_score | dist_based_score | simple
  dense_weight: 0.5         # 0.0 to 1.0
  bm25_weight: 0.5          # 0.0 to 1.0
  language: german          # a PyStemmer algorithm name
  query_expansion_num: 1    # 0 to 8
rerank:
  enabled: false
  top_n: 5                  # when enabled, top_n <= retrieval.top_k and eval.cutoff_k <= top_n
generation:
  temperature: 0.0
  max_tokens: 4096
eval:
  cutoff_k: 5               # nDCG@k, R@k, and P@k. Independent of retrieval.top_k
```

`embedding.model`, `rerank.model`, and `generation.model` are not config keys. `deepfix mutate-config` rejects them. The embedding model, reranker, and generation model come from `EMBEDDING_MODEL`, `COHERE_MODEL`, and `LLM_MODEL`. The judge comes from `LLM_JUDGE_MODEL` and the Hermes sandbox model from `HERMES_MODEL`; the sandbox also accepts `HERMES_MODEL_PROVIDER` and `HERMES_MODEL_BASE_URL`. No model id has a default in code: an empty value fails naming the variable, so set every model in `.env` (the template values in `example.env` are the deployment defaults). Run `deepfix schema --json` for the live field list and `index_time_keys`.
---
## Autonomous Optimization Workflow (Hermes Agent)
In autonomous mode, an LLM agent executes multi-cycle optimization loops guided by the DeepFix skill plugin:
1. **Observe**: Agent runs `deepfix run-diagnostic` and `deepfix eval` on the `dev` split.
2. **Match Skill**: Agent matches identified failures to modular skill instructions under `.agents/skills/`:
   - `.agents/skills/chunking-tuning/SKILL.md` (addressing context fragmentation)
   - `.agents/skills/rerank-calibration/SKILL.md` (raising `retrieval.top_k` and turning rerank on)
   - `.agents/skills/hybrid-fusion-tuning/SKILL.md` (fusion mode and weights; alias `query-rewriting`)
   - `.agents/skills/deflection-and-refusal/SKILL.md` (manual only; not used by the optimization loop)
   - `.agents/skills/retrieval-backend-selection/SKILL.md` (dense vs. bm25 vs. hybrid; alias `embedding-selection`)
3. **Act**: Agent mutates configuration (`deepfix mutate-config`), rebuilds index (`deepfix index` if needed), and re-evaluates (`deepfix eval --compare`).
4. **Decide**: If metrics improve, the mutation is adopted as the new baseline; otherwise, it is reverted.
5. **Log**: All runs, parameters, and evaluation deltas are logged to MLflow.
---
## Project Organization
```
deepfix-rag/
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
├── scripts/register_prompt.py     <- Script to register RAG prompts with MLflow
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

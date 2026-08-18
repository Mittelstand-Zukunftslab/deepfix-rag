# UX Flow Plan: DeepFix CLI & Agent-Agnostic Skill Plugin

## 1. Feature Goal (Product Terms)
Enable Data Scientists and AI Researchers to optimize RAG retrieval pipelines autonomously or interactively by decoupling **deterministic pipeline operations** (exposed as a scriptable Python CLI tool suite) from **agentic reasoning** (packaged as modular, agent-agnostic `SKILL.md` files). The Hermes agent acts as the autonomous driver, reading skill instructions and issuing CLI commands to diagnose retrieval failures, mutate configurations, and verify benchmark gains with DeepEval and MLflow.

---

## 2. Current-State UX Flow
Currently, RAG pipeline tuning and AutoML research are fragmented: developers either run manual evaluation scripts in notebooks or build ad-hoc agent loops where LLMs are tightly coupled to custom server APIs and codebases without standardized diagnostic boundaries.

```text
Data Scientist opens Jupyter Notebook / script
└─ Manually adjusts chunking parameters or embedding model in code
   └─ Executes evaluation script with DeepEval / custom scoring
      └─ Inspects log output or raw metric tables manually
         └─ Decision loop is purely human-driven; no standardized skill guidelines for agents
            └─ File: exploratory notebooks / ad-hoc scripts (no unified CLI or skill plugin)
```

---

## 3. Desired-State UX Flow

The desired experience supports two key flows: 
1. **The Human Developer UX** (inspecting, running diagnostics, and configuring).
2. **The Agent (Hermes) UX** (discovering skills, executing deterministic CLI tools, evaluating hypotheses, and logging results).

### A. End-to-End Autonomous Optimization Loop (Hermes Agent UX)
```text
Agent receives user instruction: "Optimize retrieval for dataset X"
└─ Agent loads DeepFix Plugin and discovers skills
   └─ Plugin discovery layer (`plugins/deepfix/skills/*/SKILL.md`)
      └─ Anchor: `skills/` directory structure & metadata
└─ Agent runs baseline evaluation & diagnostic check
   └─ CLI executes deterministic evaluation on pyTerrier dev split
      └─ Command: `deepfix eval --dataset <path> --config baseline.yaml --json`
      └─ Command: `deepfix run-diagnostic --dataset <path> --type retrieval --json`
         └─ Anchors: `deepfix.cli.eval:eval_cmd`, `deepfix.diagnostics.engine:run_retrieval_diagnostics`
└─ Diagnostic Engine identifies failure category (e.g. Context Fragmentation / Vocabulary Mismatch)
   └─ System returns structured JSON failure report & baseline DeepEval scores
      └─ Anchor: `deepfix.diagnostics.reporter:DiagnosticReport`
└─ Agent matches failure mode to specific skill (e.g. `skills/chunking-tuning/SKILL.md`)
   └─ Skill guides agent on mutation strategy (e.g., test recursive chunking with 512 size / 64 overlap)
      └─ Anchor: `plugins/deepfix/skills/chunking-tuning/SKILL.md`
└─ Agent executes deterministic configuration mutation
   └─ CLI validates schema and produces candidate configuration file
      └─ Command: `deepfix mutate-config --base baseline.yaml --set chunking.size=512 --output candidate_v1.yaml`
         └─ Anchor: `deepfix.config.mutator:mutate_config_cmd`
└─ Agent triggers deterministic re-indexing (if required by skill)
   └─ CLI re-builds pyTerrier vector/lexical index for candidate configuration
      └─ Command: `deepfix index --dataset <path> --config candidate_v1.yaml --index-dir ./indexes/v1`
         └─ Anchor: `deepfix.retrieval.indexer:build_index_cmd`
└─ Agent triggers candidate evaluation & metric comparison
   └─ CLI computes DeepEval scores and logs run to MLflow experiment tracking
      └─ Command: `deepfix eval --dataset <path> --config candidate_v1.yaml --compare baseline.yaml --json`
         └─ Anchors: `deepfix.eval.runner:evaluate_pipeline`, `deepfix.tracking.mlflow:log_eval_run`
└─ Agent assesses outcome against success criteria
   ├─ [Metrics Improved]: Agent accepts mutation, updates active config (`candidate_v1.yaml` becomes new baseline), and moves to next failure mode
   └─ [Metrics Degraded]: Agent rejects mutation, reverts to prior baseline, and tests alternative hypothesis from skill
└─ Optimization concludes after $\ge 5$ cycles or convergence
   └─ Agent exports final configuration & summary report
      └─ Command: `deepfix export --config best.yaml --output ./dist/rag_optimized_config.yaml`
         └─ Anchor: `deepfix.cli.export:export_config_cmd`
```

---

## 4. Architectural Boundary Decisions

| Boundary Question | Responsible Layer | Rationale |
| :--- | :--- | :--- |
| **Which layer detects the condition / failure mode?** | `deepfix.diagnostics` (CLI Diagnostic Engine) | Deterministic metric calculation (DeepEval) and IR failure classification (e.g., recall vs. precision vs. out-of-vocab) must be consistent and reproducible. |
| **Which layer reasons about remediation strategy?** | AI Agent (Hermes) via `SKILL.md` rules | The agent analyzes the structured failure report and applies heuristic problem-solving based on domain-specific skill knowledge. |
| **Which layer owns side effects & mutations?** | `deepfix.config` & `deepfix.retrieval` (CLI) | All file writes, index generation, parameter schema validations, and MLflow logging are strictly executed by CLI subcommands. |
| **Which layer updates UI / terminal status?** | `deepfix.cli.ui` (`Rich` console rendering) | Consistent formatting, progress bars for indexing, metric comparison diff tables, and clean JSON streams. |
| **Which layer manages state across cycles?** | File system (Config YAMLs & Index Dirs) + MLflow | Stateless CLI commands operate on explicit file paths (`--config`, `--index-dir`) while MLflow maintains the immutable history of runs and metrics. |

---

## 5. Implementation Anchors

### File Paths & Directory Layout
```
Mittelstand-zukunftslab/
├── deepfix/                          # Core Python Package (Deterministic CLI)
│   ├── __init__.py
│   ├── main.py                       # CLI entrypoint (Typer app)
│   ├── cli/
│   │   ├── eval.py                   # deepfix eval
│   │   ├── index.py                  # deepfix index
│   │   ├── diagnostic.py             # deepfix run-diagnostic
│   │   ├── config.py                 # deepfix mutate-config, diff-config
│   │   ├── export.py                 # deepfix export
│   │   └── ui.py                     # Rich console output formatting & JSON serializers
│   ├── config/
│   │   ├── schema.py                 # Pydantic RAG configuration schema
│   │   └── mutator.py                # Safe YAML mutation logic & diff generation
│   ├── diagnostics/
│   │   ├── engine.py                 # Failure mode categorization (recall, precision, OOV)
│   │   └── reporter.py               # JSON structured diagnostic reports
│   ├── retrieval/
│   │   ├── indexer.py                # PyTerrier index management
│   │   ├── pipeline.py               # RAG retrieval execution wrapper
│   │   └── models.py                 # vLLM / Qwen embeddings & re-ranking client
│   ├── eval/
│   │   ├── runner.py                 # DeepEval test suite runner (Faithfulness, Precision)
│   │   └── metrics.py                # Metric extraction & scoring aggregators
│   └── tracking/
│       └── mlflow.py                 # MLflow run logging, artifact storage, and span tracing
├── plugins/
│   └── deepfix/                      # Agent-Agnostic Plugin
│       ├── plugin.json               # Plugin manifest
│       └── skills/                   # Failure-mode modular skills
│           ├── chunking-tuning/
│           │   └── SKILL.md
│           ├── query-rewriting/
│           │   └── SKILL.md
│           ├── rerank-calibration/
│           │   └── SKILL.md
│           ├── embedding-model-selection/
│           │   └── SKILL.md
│           └── entity-linking-filter/
│               └── SKILL.md
└── tests/
    ├── test_cli_commands.py          # Unit tests for each CLI subcommand
    ├── test_config_mutator.py        # Tests for schema validation and config diffing
    ├── test_diagnostics.py           # Diagnostic classification tests
    └── test_hermes_integration.py    # End-to-end test verifying Hermes running optimization loop
```

### Core Function & Class Anchors
- `deepfix.config.schema.RAGConfig`: Strict Pydantic model defining valid RAG hyperparameters.
- `deepfix.config.mutator.apply_mutation(base: RAGConfig, overrides: dict) -> RAGConfig`: Validates and produces updated config.
- `deepfix.diagnostics.engine.diagnose_failures(dataset, config) -> DiagnosticReport`: Isolates queries failing precision/faithfulness thresholds.
- `deepfix.eval.runner.run_evaluation(dataset, config) -> EvalResult`: Invokes DeepEval and computes aggregate metrics.
- `deepfix.tracking.mlflow.log_experiment_run(config, metrics, artifacts, span_tree)`: MLflow telemetry integration.

### Test Coverage Plan
- **Unit / CLI Tests**: Verify every CLI subcommand (`index`, `eval`, `run-diagnostic`, `mutate-config`, `diff-config`, `export`) handles `--json` and standard flags, returns expected status codes, and catches invalid config values.
- **Diagnostics Isolation Tests**: Ensure known retrieval edge cases (e.g. out-of-vocabulary acronyms, overly fragmented text) trigger the expected failure categorization.
- **Hermes Harness Integration Test**: Run Hermes agent in test mode on the pyTerrier development split; verify that the agent parses the `SKILL.md` files, calls CLI subcommands, iterates for at least 5 cycles, and logs runs to MLflow.

---

## 6. Decision List

### Recommended Architecture
- **Stateless, Scriptable CLI**: DeepFix CLI subcommands are atomic and deterministic. They receive explicit inputs (`--config`, `--dataset`) and produce predictable outputs (`stdout`, JSON, exit codes).
- **Agent-Agnostic Skills**: Skills are authored as markdown documents (`SKILL.md`) following the universal standard, making them directly usable by Hermes, Claude, Antigravity, or other LLM agents without modification.
- **Decoupled Experiment Tracking**: MLflow is used as the external source of truth for metrics and traces, keeping the CLI lightweight.

### Alternatives Rejected
1. **Embedding the Agent inside the CLI (`deepfix agent run`)**:
   *Why rejected:* Couples the RAG tools to a single agent implementation/LLM, preventing reusability across different agent harnesses and making headless testing rigid.
2. **Interactive TUI / GUI as Primary Interface**:
   *Why rejected:* Fails to provide clean, scriptable APIs for autonomous agent execution. CLI subcommands with `--json` output allow both human and agent execution.
3. **Monolithic Skill File**:
   *Why rejected:* A single giant skill confuses LLM context windows and makes it harder to isolate distinct failure remediation strategies. Modular skills per failure mode provide clear trigger conditions.

### Open Assumptions & Validations
- **VM Connectivity**: vLLM serving Qwen 3.6 27B and Qwen3-Embedding-4B is accessible via standard REST endpoints configured via environment variables (`DEEPFIX_VLLM_URL`).
- **Benchmark Split Isolation**: The held-out test split remains untouched during the agent's optimization cycles and is only evaluated once at final sign-off.

# DeepFix Agent Guidelines: Architecture, Conventions & Design Patterns

This document serves as the mandatory reference for all AI coding agents working in the `pilotproject-deepfix` codebase. Agents must strictly adhere to existing coding conventions, modern Python 3.12 standards, idiomatic (Pythonic) practices, and robust software design patterns.

---

## 1. Architectural Principles & Layer Separation

DeepFix decouples deterministic pipeline execution from agentic reasoning and evaluation. The codebase is organized into distinct, loosely coupled layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CLI Layer (`src/deepfix_rag/cli/`)              │
│  - Typer subcommands (`dataset`, `index`, `eval`, `config`, etc.)      │
│  - Thin CLI adaptors; argument parsing & validation                    │
│  - Standardized exit codes (`0`, `1`, `2`) & JSON stdout serialization │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Invokes domain APIs
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Domain & Engine Layer (`src/deepfix_rag/`)           │
│  - `pipeline.py`: Event-driven RAG workflow (LlamaIndex `Workflow`)    │
│  - `data.py`: Strategy & Factory pattern for benchmark dataset loading │
│  - `evaluation/runner.py`: Async DeepEval scoring & MLflow tracking    │
│  - `diagnostics/`: Failure mode classification and analysis            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Configured by & Validates with
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Configuration Layer (`src/deepfix_rag/config/`)      │
│  - `schema.py`: Pydantic v2 models (`RAGConfig`, `RetrievalConfig`)    │
│  - `settings.py`: `pydantic-settings` for environment configuration    │
│  - `mutator.py`: Pure functional config mutations & structural diffs   │
└────────────────────────────────────────────────────────────────────────┘
```

### Layer Rules:
- **CLI Subcommands Must Remain Thin**: CLI modules (`src/deepfix_rag/cli/*.py`) must only handle option parsing, invoke domain classes/functions, and format output. Never put heavy business logic directly inside CLI command functions.
- **Strict Separation of I/O Streams**:
  - Machine-readable JSON payloads (when `--json` is specified) **must** go to `sys.stdout`.
  - Informational, diagnostic, and error logs **must** go to `sys.stderr` (via `LOGGER` or `typer.secho(..., err=True)` / `emit_error()`).
- **Standardized CLI Exit Codes (`src/deepfix_rag/cli/ui.py`)**:
  - `EXIT_SUCCESS = 0`: Operation completed successfully.
  - `EXIT_VALIDATION_ERROR = 1`: Invalid arguments, missing files, or Pydantic validation failures.
  - `EXIT_RUNTIME_ERROR = 2`: Unhandled runtime, network, or external backend failures.

---

## 2. Pythonic Conventions & Modern Python 3.12 Standards

Always write clean, readable, and idiomatic Python following PEP 8 and The Zen of Python ("Explicit is better than implicit", "Flat is better than nested").

### Type Annotations & Python 3.12 Features
- **Union Types**: Always use the pipe operator `|` (e.g., `str | None`, `int | float`, `Path | str`). Never use `typing.Union` or `typing.Optional`.
- **Built-in Generics**: Always use built-in collection types for type hints (e.g., `list[str]`, `dict[str, Any]`, `set[int]`, `tuple[str, ...]`). Never import `List`, `Dict`, `Set`, or `Tuple` from `typing`.
- **String Enums**: Use `enum.StrEnum` (Python 3.11+) for string-based enumerations (e.g., `RetrievalBackend`, `DataPreset`).
- **Strict Typing**: Fully annotate all function signatures (all parameters and return types).

```python
# GOOD: Modern, clean Python 3.12 typing
from enum import StrEnum
from pathlib import Path
from typing import Any

class BackendType(StrEnum):
    HYBRID = "hybrid"
    DENSE = "dense"

def process_records(
    file_path: Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ...

# BAD: Outdated typing constructs
from typing import Dict, List, Optional, Union

def process_records(file_path: Union[str, Path], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    ...
```

### Idiomatic Practices
- **Path Handling**: Always use `pathlib.Path` for filesystem path manipulation and checks (`path.exists()`, `path / "sub"`, `path.read_text(encoding="utf-8")`). Avoid legacy `os.path` operations.
- **Context Managers**: Always use context managers (`with` / `async with`) when opening files, acquiring locks, or managing MLflow/database sessions.
- **Comprehensions & Generators**: Prefer list/dict/set comprehensions or generator expressions over accumulator loops with manual `.append()`, provided readability is preserved.
- **Guard Clauses & Early Returns**: Structure functions with guard clauses and early exits to avoid deep indentation blocks.
- **Explicit Error Handling**: Catch specific exceptions (`ValueError`, `FileNotFoundError`, `KeyError`) rather than bare `except Exception:`, unless explicitly converting unhandled exceptions at boundary layers.

---

## 3. Pythonic Design Patterns

Apply software design patterns natural to Python and aligned with the existing codebase:

### Strategy & Factory Pattern (Data Ingestion)
- Define abstract base classes using `abc.ABC` and `@abstractmethod` (e.g., `BaseLoaderStrategy`).
- Implement concrete strategy loaders (e.g., `BundestagLoader`, `LegalRagBenchLoader`, `HuggingFaceLoader`, `LocalFileLoader`).
- Provide dynamic dispatch / factory routing in `DataLoader` based on `DataLoaderConfig` and `DataPreset.resolve()`.

```python
from abc import ABC, abstractmethod
from llama_index.core.schema import Document

class BaseLoaderStrategy(ABC):
    @abstractmethod
    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        """Load and return corpus documents."""
        raise NotImplementedError

    @abstractmethod
    def load_qa(self, config: DataLoaderConfig) -> list[dict[str, Any]]:
        """Load and return QA pairs."""
        raise NotImplementedError
```

### Event-Driven Workflow Pattern (RAG Pipeline)
- For complex, multi-stage async pipelines (Ingestion -> Retrieval -> Reranking -> Synthesis), utilize LlamaIndex's event-driven `Workflow`.
- Define explicit typed events (subclassing `llama_index.core.workflow.Event`).
- Implement step methods decorated with `@step`, accepting typed events and returning next-stage events or `StopEvent`.

### Dependency Injection
- Pass dependencies (such as `RAGConfig`, `ProxyJudgeLLM`, `DataLoader`, or custom LLM instances) explicitly into classes/functions via `__init__` or arguments.
- Avoid hidden global mutable state or hardcoded configuration parameters.

### Schema Validation & Immutability (Pydantic v2)
- Define strict domain configurations using Pydantic v2 `BaseModel` and `Field` with explicit constraints (`ge=...`, `le=...`, `default_factory=...`).
- Use `@model_validator(mode="after")` for cross-field inference and validation.
- Treat configuration objects as immutable data; produce new validated instances via functional transformations (e.g. `apply_mutation(base_config, overrides) -> RAGConfig`).

---

## 4. Testing Conventions & Pytest Execution

The project uses [pytest](https://docs.pytest.org/) with registered markers in `pyproject.toml` to support selective, fast execution.

### Pytest Markers

| Marker | Description | Example Target |
| :--- | :--- | :--- |
| `unit` | Fast unit tests with no network or disk I/O | Schema validation, mutators, helper functions |
| `cli` | Typer CLI commands, argument parsing, JSON contract & exit codes | `test_cli.py`, `test_dataset_cli.py` |
| `integration` | Tests requiring local filesystem, LanceDB tables, or loaders | `test_index_creation.py`, local loader tests |
| `e2e` | Full end-to-end pipeline ingestion, embedding & retrieval | `test_end_to_end.py` |
| `network` | Tests interacting with live Hugging Face Hub APIs | Live dataset download tests |

### Test Execution Commands

```bash
# Run fast tests (skipping slow network and live e2e tests)
uv run --env-file .env pytest -v -m "not network and not e2e"

# Run fast unit tests only
uv run --env-file .env pytest -v -m "unit"

# Run CLI tests only
uv run --env-file .env pytest -v -m "cli"

# Run end-to-end pipeline tests
uv run --env-file .env pytest -v -m "e2e"

# Run full test suite
uv run --env-file .env pytest -v
```

### Shared Fixtures (`tests/conftest.py`)

Always reuse standard fixtures defined in [`tests/conftest.py`](tests/conftest.py):
- **`cli_runner`**: Provides an isolated `typer.testing.CliRunner` instance.
- **`default_config_path`**: Path to root [`configs/default.yaml`](configs/default.yaml).
- **`sample_corpus_path`**: Path to [`tests/data/sample_corpus.jsonl`](tests/data/sample_corpus.jsonl).
- **`sample_qa_path`**: Path to [`tests/data/sample_qa.jsonl`](tests/data/sample_qa.jsonl).
- **`default_rag_config`**: Fresh default `RAGConfig` instance.
- **`sample_rag_config`**: Pre-configured `RAGConfig` referencing sample benchmark fixtures.
- **`isolated_mlflow_env`**: Isolated local SQLite MLflow tracking environment.
- **`populated_sample_index_dir`**: Temporary LanceDB directory pre-populated with sample corpus.

### Test Isolation & Real Pipeline Testing (No Mocks)
- Use `tmp_path` fixture for any file/directory writes during tests.
- **Strictly No Mocks**: Do NOT use `unittest.mock`, `Mock`, `AsyncMock`, or `monkeypatch` to stub domain logic or pipeline execution. Tests must execute against real code, deterministic local fixtures, or live services.
- For fast, deterministic runner and CLI tests without external LLM API dependencies, use `--retrieval-only` mode with sample benchmark fixtures (`sample_qa_path`, `sample_corpus_path`, `populated_sample_index_dir`).

---

## 5. Code Quality, Formatting & Tooling

DeepFix enforces strict code style, import sorting, and linting rules via **Ruff** configured in `pyproject.toml`:
- **Line Length**: 99 characters.
- **Import Sorting (`isort`)**: First-party package is `deepfix_rag` (`force-sort-within-sections = true`).
- **Lint Rules**: `E` (pycodestyle errors), `F` (Pyflakes), `I` (isort). Allowed exemptions: `B008` (Typer default values in function arguments) and `E501` (long docstrings/lines).

### Formatting & Linting Commands

```bash
# Check code style and imports with ruff
uv run ruff check

# Automatically format and fix issues
uv run ruff check --fix
uv run ruff format
```

---

## 6. Agent Workflow Checklist

When executing tasks in this repository, follow these steps:
1. **Understand Architectural Boundaries**: Identify whether the task belongs in `cli/`, `config/`, `pipeline.py`, `data.py`, or `evaluation/`.
2. **Follow Existing Patterns**: Use Pydantic schemas for data modeling, Strategy/Factory for polymorphic loaders, and Typer/UI conventions for CLI commands.
3. **Write Idiomatic Python 3.12**: Apply strict type hints (`X | Y`, `list[T]`, `StrEnum`), `pathlib.Path`, and clean control flow.
4. **Format & Lint**: Run `uv run ruff check --fix` and `uv run ruff format`.
5. **Verify with Tests**: Run the targeted pytest command (`uv run --env-file .env pytest -v -m "not network and not e2e"`).
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


# ReadMe
You can get more info in README.md
# DeepFix Agent Guidelines: Architecture, Conventions & Design Patterns

This document is the mandatory reference for every contributor — human or AI — working in the `deepfix-rag` codebase. It covers how work is planned, how it is committed, the architecture the code must respect, and the conventions it must follow.

---

## 1. How work is done

### Branch and commits

- Work on `feat/hermes-optimizer`. **Commit straight to that feature branch.** Do not push to `dev` or `main`, and do not open a pull request unless someone explicitly asks for one.
- **One user story at a time.** Do not batch stories. Each story is implemented, tested, and committed on its own before the next one starts, so a review can be a single commit and a broken story can be reverted alone.
- Commit messages name the story and say what changed, e.g. `CHAT-006: bound the chat, expose the audit trail, and prune it hourly`.
- Do not amend or force-push a shared branch. Do not merge a pull request.
- Never commit a secret, a real `.env`, a generated run artifact, an index, or a dataset. `example.env` and the Compose files carry key names and defaults only.

### PRDs and stories

Product work is planned in `tasks/` before it is implemented, using four skills from [awesome-skills](https://github.com/Mittelstand-Zukunftslab/awesome-skills):

| Skill | When it is used |
| :--- | :--- |
| `create-prd` | Turn a brief or a problem statement into `tasks/prd-<feature>.md`. Asks three to five clarifying questions first; the owner answers them, and the answers become the PRD's decisions. |
| `validate-prd` | Review a PRD draft against the skill's dimensions before stories are cut. Record the verdict and the actions taken in the PRD itself. |
| `create-story` | Break an accepted PRD into `tasks/stories-<feature>/`, one file per story, each independently testable and committable. |
| `validate-story` | Review each story's acceptance criteria and test plan. The result lands beside the story as `*-validation.md`. |

This repository keeps product documents in `tasks/`, so the skills' default `docs/` output paths are overridden there. `tasks/stories-admin-hermes-chat/README.md` records how that was done for one feature and is the model to copy.

A PRD is a plan, not an implementation. It states what will be built, which decisions are settled, and what is explicitly out of scope. An accepted PRD is followed by stories; a story is followed by code.

### Prefer established libraries over hand-rolled code

Reach for the library the project already depends on before writing the mechanism yourself:

- Pipeline and retrieval: LlamaIndex (`Workflow`, retrievers, node parsers, response synthesizers).
- Evaluation: DeepEval and `pyterrier`.
- Config and validation: Pydantic v2 and `pydantic-settings`.
- HPO: Optuna.
- Scheduling: APScheduler.
- HTTP: `httpx`.
- Tracking: MLflow.
- Web: TanStack Start/Router/Query, Better Auth, Assistant UI, the Vercel AI SDK, Zod, shadcn/ui, OpenAPI-generated client.

A hand-written implementation is justified when a library does not exist for the job, when the library's behaviour is wrong for this deployment (documented inline, as with the OpenRouter rerank client), or when the mechanism is a few lines and a dependency would be larger than the code. Say which case applies in the commit or the docstring.

### Tests stay minimal and meaningful

- Write integration and end-to-end tests for behaviour a user or an operator depends on. Do not write a test per function, and do not restate a constant in a test.
- `network` tests hit live Hugging Face or OpenRouter endpoints. They are deselected by default and only run when a command names `network` or `e2e`, or points at their file. Keep that property.
- No mocks of domain logic. Tests run against real code and deterministic local fixtures. A test that needs a network service belongs under `network`, not behind a stub.
- Use `tmp_path` for every file or directory a test writes.
- Reuse the fixtures in `tests/conftest.py`.
- Every test module declares its marker (`unit`, `cli`, `integration`, `e2e`, `network`, `api`) because the suite runs with `--strict-markers`.

### Keep the Hermes secret scrub intact

`optimization._secrets` and `optimization._redact` remove every configured secret from step lines, heartbeat lines, terminal output, and stored reasons before any of it reaches a log, a database column, or a chat reply. `docker/hermes/deepfix_run_tag` and `docker/hermes/bin/deepfix` carry the same key list. If you add a secret to the system, add it to all three lists in the same change. Never remove a name from one of them to make an output easier to read.

---

## 2. Architecture and layer separation

```
┌────────────────────────────────────────────────────────────────────────┐
│ CLI layer (`src/deepfix_rag/cli/`)                                     │
│  - Typer subcommands (dataset, index, eval, gate, hpo, …)              │
│  - Thin adaptors: parsing, domain call, output formatting              │
│  - Exit codes 0 / 1 / 2 and JSON on stdout                             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ calls domain APIs
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Domain and engine layer (`src/deepfix_rag/`)                           │
│  - `pipeline.py`: LlamaIndex Workflow RAG pipeline                     │
│  - `data.py`: strategy/factory dataset loading                         │
│  - `evaluation/runner.py`: async DeepEval scoring and MLflow tracking  │
│  - `indexing.py`: blue-green workspace builds                          │
│  - `optimization.py`, `promotion.py`, `optimization_schedule.py`       │
│  - `admin_chat.py`: the chat store, audit trail, limits, and prompt    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ configured by, validated with
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Configuration layer (`src/deepfix_rag/config/`)                        │
│  - `schema.py`: Pydantic v2 models (`RAGConfig`, `RetrievalConfig`, …) │
│  - `settings.py`: `pydantic-settings`, `require_model`, logger         │
│  - `mutator.py`: pure config mutation and structural diff              │
│  - `hpo.py`: Optuna search space and study config                      │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ HTTP boundary (`src/deepfix_rag/api/`) and UI (`web/`)                 │
│  - FastAPI routes stay thin adaptors over the domain layer             │
│  - `web/` talks only through the generated OpenAPI client              │
└────────────────────────────────────────────────────────────────────────┘
```

### Layer rules

- **CLI subcommands stay thin.** A module in `src/deepfix_rag/cli/` parses options, calls a domain function or class, and formats the result. Business logic belongs in the domain layer, not in the command function.
- **API routes stay thin.** A route in `src/deepfix_rag/api/routes/` authenticates, validates via Pydantic, calls the domain layer, and maps domain exceptions to HTTP status codes. No SQL and no scoring inside a route.
- **Strict separation of I/O streams.** Under `--json`, machine-readable payloads go to `sys.stdout` and every diagnostic, progress, and error line goes to `sys.stderr` (`LOGGER`, `print_info()`, `emit_error()`). A caller must be able to pipe stdout straight into `jq`.
- **Standardized exit codes** (`src/deepfix_rag/cli/ui.py`):
  - `EXIT_SUCCESS = 0` — the operation completed.
  - `EXIT_VALIDATION_ERROR = 1` — bad arguments, missing files, schema validation failures, a missing model id.
  - `EXIT_RUNTIME_ERROR = 2` — unhandled runtime, network, or backend failure.
- **The domain layer owns the invariants.** Holdout scoring, the promotion gate, `apply_candidate` as the only writer of `configs/champion.yaml` after seeding, the chat audit trail, and the single-run rule all live in the domain layer. Neither the CLI nor a route nor an agent may bypass them.
- **Model ids are deployment configuration.** No model id has a default in the package. A consumer calls `require_model("<NAME>")` and fails naming the variable when it is empty.

---

## 3. Python conventions (3.12)

Follow PEP 8 and the Zen of Python: explicit over implicit, flat over nested, readable over clever.

### Type hints

- Union types use the pipe operator: `str | None`, `int | float`, `Path | str`. Never `typing.Optional` or `typing.Union`.
- Built-in generics: `list[str]`, `dict[str, Any]`, `set[int]`, `tuple[str, ...]`. Never `typing.List` and friends.
- String enums use `enum.StrEnum`.
- Annotate every parameter and every return type.

```python
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
```

### Idiomatic practice

- `pathlib.Path` for every filesystem operation; no `os.path`.
- Context managers for files, locks, datastores, and MLflow sessions.
- Comprehensions and generator expressions over manual accumulator loops, when the result stays readable.
- Guard clauses and early returns over nested `if` blocks.
- Catch specific exceptions. A bare `except Exception` is allowed only at a boundary that converts an unexpected failure into a typed error, and it must log.
- A module docstring explains the invariant or the non-obvious constraint the module exists for, not what each function does.
- Comments explain intent, trade-offs, and constraints the code cannot express. Do not narrate the code.

---

## 4. Design patterns in use

### Strategy and factory (data ingestion)

`BaseLoaderStrategy` (`abc.ABC` + `@abstractmethod`) with `BundestagLoader`, `LegalRagBenchLoader`, `HuggingFaceLoader`, and `LocalFileLoader`; `DataLoader` dispatches on `DataLoaderConfig` and `DataPreset`.

### Event-driven workflow (RAG pipeline)

The pipeline is a LlamaIndex `Workflow` with typed `Event` subclasses and `@step` methods, so ingestion, retrieval, reranking, and synthesis stay separate stages with typed hand-offs.

### Dependency injection

Pass dependencies (`RAGConfig`, `IndexCoordinator`, judge LLM, `DataLoader`) through `__init__` or arguments. No hidden global mutable state and no hardcoded configuration constants where a setting belongs.

### Schema validation and immutability (Pydantic v2)

`BaseModel` with explicit `Field` constraints, `@model_validator(mode="after")` for cross-field rules, and functional transformation of configuration (`apply_mutation(base, overrides) -> RAGConfig`) rather than in-place edits.

### One owner per invariant

Each rule has exactly one module that enforces it: promotion in `promotion.py`, run lifecycle in `optimization.py`, scheduling in `optimization_schedule.py`, chat state in `admin_chat.py`, the bundle layout in `run_bundle.py`. A second implementation of an invariant is a defect, even if it agrees with the first today.

---

## 5. Testing

Markers are registered in `pyproject.toml`:

| Marker | Scope | Typical target |
| :--- | :--- | :--- |
| `unit` | No network, no heavy I/O | Schema, mutators, helpers, the skills contract |
| `cli` | Typer commands, JSON contract, exit codes | `test_cli.py`, `test_dataset_cli.py` |
| `integration` | Local filesystem, LanceDB, loaders, the API app | `test_index_creation.py`, `test_api_*.py` |
| `e2e` | Full ingestion, embedding, retrieval | `test_end_to_end.py` |
| `network` | Live Hugging Face or OpenRouter | Dataset download cases |
| `api` | FastAPI routes, schemas, OpenAPI contract | `test_api_skeleton.py` |

`tests/conftest.py` deselects every `network` case unless the run names `network` or `e2e` in `-m`, or points at a test file by path. A command-line `-m` replaces `addopts` rather than intersecting with it, so without that guard `pytest -m cli` reached live endpoints.

```bash
# Fast suite: the default thing to run
uv run --env-file .env pytest -v -m "not network and not e2e"

uv run --env-file .env pytest -v -m "unit"
uv run --env-file .env pytest -v -m "cli"
uv run --env-file .env pytest -v -m "api"
uv run --env-file .env pytest -v -m "e2e"
```

### Fixtures

Reuse `tests/conftest.py`:

- `cli_runner` — isolated `typer.testing.CliRunner`.
- `default_config_path` — `configs/default.yaml`.
- `sample_corpus_path`, `sample_qa_path` — `tests/data/*.jsonl`.
- `default_rag_config` — a fresh `RAGConfig`.
- `sample_rag_config` — a `RAGConfig` pointing at the sample fixtures.
- `populated_sample_index_dir` — a temporary LanceDB directory built from the sample corpus.
- `identity` — an Ed25519 signing key and the matching verifier for API tests.

### Isolation

- `tmp_path` for every write.
- No `unittest.mock`, `Mock`, `AsyncMock`, or `monkeypatch` stubbing domain logic or pipeline execution. Use real code, deterministic local fixtures, or a live service marked `network`.
- For fast, deterministic runner and CLI coverage without model calls, use `--retrieval-only` with the sample fixtures.

---

## 6. Code quality and tooling

Ruff is configured in `pyproject.toml`:

- Line length 99.
- First-party packages `deepfix_rag` and `tests`, sorted within sections.
- Lint rules `E`, `F`, `I`; `B008` (Typer defaults) and `E501` (long docstrings) exempted.

```bash
uv run ruff check --fix
uv run ruff format
```

The web app has its own checks, run from `web/`:

```bash
bun run typecheck && bun run lint && bun run test
```

`web/openapi.json` and `web/src/client/` are generated. Never edit them; run `npm run generate:api` after any change to an API route or Pydantic model. CI regenerates both and fails if they differ from what is committed.

CI (`.github/workflows/ci.yml`) runs three jobs on `main`, `dev`, `feature/ui`, and `feat/hermes-optimizer`: `api` (ruff on the API package plus the `api` marker), `cli` (ruff everywhere plus `unit`/`cli` plus the `uv tool install` adopter check), and `web` (schema regeneration, typecheck, lint, test).

---

## 7. Documentation

- The code is the source of truth. When docs and code disagree, fix the docs.
- `README.md` is the front door: what DeepFix is, how to install it, how to use it, and how it is deployed.
- `AGENTS.md` (this file) is how to work on it.
- `docs/` holds operator runbooks and deployment notes.
- `configs/README.md` describes every shipped configuration.
- `tasks/` holds PRDs, stories, plans, and implementation notes, in that order of authority: a PRD describes intent, a story describes a unit of work, and a note records what was learned.
- Document env **key names and purpose**, never values.
- No hostnames, IP addresses, or credentials in documentation.

---

## 8. Checklist for a change

1. Decide the layer: `cli/`, `api/`, `config/`, a domain module in `src/deepfix_rag/`, or `web/`.
2. Follow the existing pattern for that layer; do not invent a second one.
3. Check whether a dependency already does this.
4. Write idiomatic Python 3.12, or TypeScript that passes the web app's lint and typecheck.
5. Add or extend a meaningful test; keep it minimal.
6. Run `uv run ruff check --fix`, `uv run ruff format`, and the fast pytest selection. Run the web checks if `web/` changed.
7. Update the docs the change invalidates.
8. Commit on `feat/hermes-optimizer`, one story per commit.

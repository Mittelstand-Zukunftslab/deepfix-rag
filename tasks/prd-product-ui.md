# PRD: DeepFix Product UI

## Introduction

DeepFix today is a deterministic CLI (`deepfix`) plus an agent loop. Engineers load data, build a LanceDB index, run retrieval or full RAG, score results with DeepEval, and let Hermes optimize the pipeline. That is powerful for the team and invisible to everyone else.

This feature turns that pipeline into a small internal product. A FastAPI service wraps the existing domain layer and publishes its OpenAPI schema. A TanStack Start app is the interface. Its TypeScript client is generated from that schema with `@hey-api/openapi-ts`, so the screens never hand-write request types. Signed-in members share **one workspace corpus**: they upload text, Markdown, and PDF files, search the index, and ask questions. Thumbs up and thumbs down on retrieved passages and on answers are stored, traced in MLflow, and turned into an evaluation dataset. Admins, and only admins, open an Optimization section that shows the nightly schedule and can start that same job immediately. The job runs Hermes in this API process through its Python library, not through a separate gateway.

The feedback loop is the point of the feature, so this PRD is strict about three things:

1. **Votes must become labels that can judge a different config.** A passage with more up-votes than down-votes is relevant. A passage with more down-votes than up-votes is negative. Nothing the current champion produced on its own (its unvoted top hit, its answer text) is ever used as a label, because that would score every alternative config as worse than the champion.
2. **Optimization must not grade itself.** Feedback is split by question into a `dev` set Hermes optimizes on and a `holdout` set only the API can read. A candidate is promoted only if it also holds up on `holdout`.
3. **The API owns run state and promotion.** Hermes proposes and evaluates candidates with the existing CLI and skills. The API decides whether a run starts, whether a candidate is promoted, and when the live index is rebuilt.

## Goals

- Give members a signed-in app where they can upload documents into one shared corpus, search it, and ask grounded questions.
- Record thumbs up and thumbs down on passages and answers, never lose a vote, and mirror every vote to MLflow as feedback on the trace of the request it judges.
- Turn passage votes into a `dev`/`holdout` evaluation dataset that `deepfix eval` can score for any candidate config.
- Let admins inspect the nightly Hermes optimization job, change its schedule, and start it on demand.
- Promote a config only when it beats the champion on `dev` by the existing quality gate and does not regress on `holdout`.
- Keep product API code thin: FastAPI calls `deepfix_rag` domain functions. The embedded Hermes agent calls the `deepfix` CLI for index, eval, and config changes.
- Generate the frontend API client from FastAPI's OpenAPI schema with `@hey-api/openapi-ts`. The schema is the contract.
- Ship a pilot that one team runs on one server: the API process embeds Hermes, and Docker Compose still provides MLflow and the diagnostic server. Not a multi-tenant SaaS.

## User Stories

Stories are ordered by dependency. US-001 to US-003 unblock the API and the optimization loop. US-004 and US-005 unblock the screens.

### US-001: Workspace query service in the domain layer
**Description:** As a developer, I need a long-lived service that loads an index once and answers retrieval and RAG requests, so the API does not rebuild or reload the index on every request.

**Context:** `execute_pipeline` runs ingestion on every call and returns only passages. The generated answer is written to the workflow context (`synthesized_response`) and not returned. Retrieval-only mode is a config flag (`RAGConfig.retrieval_only`), not a per-call option.

**Acceptance Criteria:**
- [ ] A new domain module (for example `src/deepfix_rag/service.py`) exposes a class that is constructed with a `RAGConfig` and an index directory, loads the existing index once, and keeps it in memory.
- [ ] `async retrieve(query: str) -> list[RetrievalResult]` runs retrieval and reranking only, regardless of `config.retrieval_only`, and never calls the generation model.
- [ ] `async answer(query: str) -> RAGResult` runs retrieval, reranking, and synthesis and returns both `retrieval_results` and `generated_answer`.
- [ ] `RAGResult.generated_answer` is annotated `str | None` (not `Optional[str]`), matching the repo typing rules.
- [ ] Every `RetrievalResult` keeps its parent `doc_id` (via `DataLoader.parent_doc_id`), score, and chunk text, in rank order.
- [ ] Calling `retrieve` twice on the same service does not rebuild or reload the index. A test asserts this without mocks by timing or by counting index loads through a real counter on the service.
- [ ] Existing CLI commands (`index`, `eval`) keep working unchanged.
- [ ] Tests use `populated_sample_index_dir` and `sample_rag_config`; no mocks of the pipeline.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-002: Multi-label, reference-optional eval records
**Description:** As an AI engineer, I need `deepfix eval` to accept several relevant documents, explicit negative documents, and questions without a reference answer, so product feedback can be scored without inventing labels.

**Context:** `QARecord` has one required `doc_id` and one required `answer`. The runner builds `ground_truth_doc_ids = [qa.doc_id]` and treats a record with no ground-truth documents as unanswerable.

**Acceptance Criteria:**
- [ ] `QARecord` gains `doc_ids: list[str]`, `negative_doc_ids: list[str] | None`, and makes `answer: str | None`. A record loaded with a single `doc_id` string produces `doc_ids=[doc_id]` and `negative_doc_ids=None`, so every existing benchmark file loads unchanged.
- [ ] JSONL rows may carry either `"doc_id": "<id>"` or `"doc_ids": ["<id>", ...]`. A row with both keys is rejected with exit code 1. The same `doc_id` in both `doc_ids` and `negative_doc_ids` is rejected with exit code 1.
- [ ] When `negative_doc_ids` is absent, scoring stays as it is today: `doc_ids` are relevant, and every retrieved document outside that list is non-relevant.
- [ ] When `negative_doc_ids` is present, labels are binary and judged-only. `doc_ids` are relevant (label 1). `negative_doc_ids` are negative (label 0). A retrieved document in neither list is unjudged: it is left out of precision and nDCG. Retrieving a negative document lowers precision and nDCG. Recall uses only `doc_ids`. A row with an empty `doc_ids` list omits recall and is not treated as unanswerable.
- [ ] For rows where `answer` is missing, metrics that require a reference answer (answer correctness and any DeepEval metric that needs `expected_output`, such as contextual precision and contextual recall) are skipped for that row and not averaged into the summary. Reference-free metrics (faithfulness, answer relevancy) still run.
- [ ] A metric skipped for every row is absent from `eval --compare` `delta`, and the optimization-loop skill already treats a missing delta as "not an improvement".
- [ ] Existing unanswerable handling is unchanged: a row is unanswerable only if it is explicitly marked so, not merely because `answer` is missing.
- [ ] Unit tests cover single-`doc_id`, multi-`doc_ids`, missing-`answer`, both-keys rows, a retrieved negative lowering precision, an unjudged hit left out of precision, a negative-only row that is not unanswerable, and an unchanged benchmark row that has no `negative_doc_ids`.
- [ ] `uv run --env-file .env pytest -v -m "not network and not e2e"` passes.

### US-003: Embed Hermes in the API process
**Description:** As a developer, I want Hermes to run inside the API server through its Python library, so optimization does not depend on the Hermes gateway.

**Context:** [Using Hermes as a Python library](https://hermes-agent.nousresearch.com/docs/guides/python-library#basic-usage) exposes `AIAgent` from `run_agent`. `run_conversation()` returns `final_response` and `messages`. Hermes does not publish a wheel: the runtime is a checkout of `https://github.com/NousResearch/hermes-agent.git` on `PYTHONPATH`. An agent instance is not safe to share across runs.

**Acceptance Criteria:**
- [ ] The API server process can `from run_agent import AIAgent`. A pinned Hermes commit is recorded in `tasks/notes-hermes-integration.md`, along with the checkout path used in this environment.
- [ ] A domain helper builds one new `AIAgent` per optimization run with `quiet_mode=True`, `skip_memory=True`, `max_iterations=15`, and `api_key` / `base_url` / `model` taken from the existing LLM settings. It calls `run_conversation(user_message=..., task_id=run_id)` and returns `final_response`. It does not call the Hermes gateway on ports 8642 or 9119.
- [ ] The agent's terminal tool is enabled. Indexing, eval, and config mutation happen only when the agent shells out to the `deepfix` CLI (`uv tool install` of this repo, same git revision as the API, `deepfix` on `PATH`). The agent does not import `deepfix_rag`.
- [ ] The helper runs the agent with working directory `/workspace`. The holdout directory is not in the prompt and not in the environment passed to the agent. Promotion stays in API code after `run_conversation` returns.
- [ ] A test, skipped only when `run_agent` cannot be imported, runs one agent turn whose prompt is to execute `deepfix --help` and asserts the command's usage text appears in `final_response`.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-004: FastAPI service shell
**Description:** As a developer, I want a FastAPI app that boots beside the existing package so the product has an HTTP boundary around the domain code.

**Acceptance Criteria:**
- [ ] The app lives in `src/deepfix_rag/api/` and starts with `uv run --env-file .env uvicorn deepfix_rag.api.app:app`.
- [ ] `GET /health` returns HTTP 200 and `{"status": "ok"}` without calling the LLM, embeddings, MLflow, or Hermes.
- [ ] Route handlers parse input, call domain code (US-001 service, data loaders, eval runner), and serialize output. No retrieval, indexing, or evaluation logic lives in route functions.
- [ ] Errors use `{"detail": "<message>"}` with 400 for bad input, 401 for no session, 403 for wrong role, 404 for unknown ids, 409 for conflicting state, and 503 when a required dependency (ready index, or the embedded Hermes import) is unavailable. That body is a Pydantic model named in the route's `responses`, so it appears in the schema.
- [ ] Every public route declares a Pydantic request body (or form fields) and a Pydantic response model. No public route returns an untyped `dict`.
- [ ] Every public route sets an explicit `operation_id` in snake_case (`health`, `register`, `login`, and so on). FastAPI must not invent ids from function names.
- [ ] The app has no `/internal` routes. Optimization calls Python functions in-process. `GET /openapi.json` lists only the public product routes, and a test asserts no path starts with `/internal`.
- [ ] `uv run python -m deepfix_rag.api.export_openapi` writes that document to `web/openapi.json` by calling `app.openapi()`. It does not start a server and does not load an index.
- [ ] An `api` pytest marker is registered in `pyproject.toml` and used for all API tests.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-005: TypeScript client generated from the FastAPI schema
**Description:** As a frontend developer, I want the API client generated from FastAPI's OpenAPI schema so request and response types stay in lockstep with the Python models.

**Acceptance Criteria:**
- [ ] `web/openapi-ts.config.ts` uses `@hey-api/openapi-ts` with `input: './openapi.json'` (the file exported in US-004) and `output: './src/client'`.
- [ ] Plugins are `@hey-api/typescript`, `@hey-api/sdk`, and `@hey-api/client-fetch`. No second HTTP client plugin is enabled.
- [ ] The generated fetch client is configured once, in `web/src/api.ts`, with the API base URL from the environment and `credentials: 'include'` so the session cookie is sent.
- [ ] `npm run generate:api` in `web/` exports `web/openapi.json` and then runs `openapi-ts`. `web/src/client` is produced only by that command. Nobody edits files in that directory by hand.
- [ ] CI regenerates the client and fails if `web/openapi.json` or `web/src/client` differs from what is committed.
- [ ] Application code under `web/src` imports SDK functions from `web/src/client`. A check fails if any file outside `web/src/client` calls `fetch` against the API origin.
- [ ] The generated SDK contains no function whose path starts with `/internal`.
- [ ] Typecheck passes.

### US-006: Email and password accounts with roles
**Description:** As a member, I want an account so my uploads and votes are attributed to me. As an admin, I want optimization controls hidden from members.

**Acceptance Criteria:**
- [ ] `POST /auth/register` accepts `email` and `password` (minimum 8 characters) and creates a user with role `member`.
- [ ] If the registering email equals `DEEPFIX_BOOTSTRAP_ADMIN_EMAIL`, the user receives role `admin`. There is no "first user becomes admin" rule.
- [ ] `POST /auth/login` sets an httpOnly, `SameSite=Lax` session cookie (`Secure` when served over HTTPS). `POST /auth/logout` clears it. `GET /auth/me` returns `id`, `email`, and `role`.
- [ ] Passwords are stored only as Argon2 hashes. No endpoint returns the hash.
- [ ] Sessions expire 14 days after creation.
- [ ] `PATCH /admin/users/{user_id}` with `{"role": "admin" | "member"}` changes a role. Members receive 403. Demoting the last remaining admin returns 409.
- [ ] Sign-in, sign-out, and register screens exist in the TanStack Start app. After sign-in, the header shows the user's email and shows an Admin link only when `role` is `admin`.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-007: Upload text, Markdown, and PDF into the shared corpus
**Description:** As a member, I want to add documents to the one shared workspace so everyone can search the same corpus.

**Acceptance Criteria:**
- [ ] `POST /documents` accepts `multipart/form-data` with one file. Allowed extensions: `.txt`, `.md`, `.pdf`. Others return 400.
- [ ] Maximum upload size is 20 MB. Larger files return 400 with a message that states the limit.
- [ ] `.txt` and `.md` are decoded as UTF-8. PDF text is extracted with `pypdf` (added as a dependency). A PDF with no extractable text is stored with status `failed` and reason "No extractable text. Scanned PDFs are not supported."
- [ ] Each document gets `doc_id = "ws_" + uuid4().hex`. The id never contains `::`, so `DataLoader.parent_doc_id` returns it unchanged.
- [ ] Each document record stores `doc_id`, original filename, media type, extracted text, status (`pending`, `indexing`, `indexed`, `deleting`, `failed`), `index_error`, `uploaded_by`, `created_at`, `indexed_at`, and `deleted_at`.
- [ ] `GET /documents` returns non-deleted documents for any signed-in user, newest first.
- [ ] The uploader or an admin can `DELETE /documents/{doc_id}`. Other members receive 403. Deletion sets `deleted_at` (soft delete) so existing votes still resolve to a filename.
- [ ] The Library screen lists filename, uploader email, status, and upload time, and offers Upload and, where allowed, Delete.
- [ ] Empty state: "No documents yet. Upload a text, Markdown, or PDF file to start."
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-008: Incrementally maintain the shared index
**Description:** As a member, I want each uploaded document indexed independently so it becomes searchable without rebuilding unrelated documents.

**Acceptance Criteria:**
- [ ] The serving index normally lives at `<workspace>/indexes/current/`. Uploading one document creates a background job for that `doc_id`; it does not re-read, re-chunk, or re-embed any other document.
- [ ] Before indexing, the API writes the extracted source to `<workspace>/corpus/<doc_id>.json` with `{"doc_id", "text", "filename"}`. The corpus directory is the reproducible source for a later full rebuild.
- [ ] The per-document job chunks only that document with the active champion config, preserves parent `doc_id` on every chunk, embeds those chunks, and inserts them into the current LanceDB table.
- [ ] A document moves `pending` → `indexing` → `indexed`. On failure it moves to `failed`, stores `index_error`, and leaves all other documents and chunks unchanged. Retrying the document is idempotent: existing chunks for that `doc_id` are removed before its replacement chunks are inserted.
- [ ] Deleting a document moves it to `deleting`, removes every chunk whose parent metadata `doc_id` matches, removes `<workspace>/corpus/<doc_id>.json`, then sets `deleted_at`. A failed index deletion leaves the document visible as failed/deleting to admins and is retried; it is not reported as successfully deleted.
- [ ] Per-document mutations are serialized through one index-writer queue. Search and Ask continue reading the current index while a mutation runs. The service refreshes its BM25/hybrid lexical retriever after a successful insert or delete so lexical results include the change.
- [ ] Workspace indexing supports `retrieval.backend` values `dense`, `hybrid`, and `bm25` only. A champion config with `graph` or `mixed` is rejected (see US-014), because per-document graph mutation and relation cleanup are out of scope.
- [ ] A **full blue-green rebuild** occurs only when there is no current index, when an index-time configuration key changes (chunking strategy/size/overlap, embedding model, vector store, or retrieval backend), or when an admin explicitly requests repair. It builds all non-deleted documents from `<workspace>/corpus/` into `<workspace>/indexes/builds/<build_id>/`.
- [ ] A full rebuild never writes into the serving directory. On success, the API atomically switches the current-index pointer and reloads US-001; requests already in flight finish on the old index. On failure, the old index and config keep serving. The newest successful previous build is retained for rollback.
- [ ] `POST /admin/index/rebuild` starts that repair rebuild. Members receive 403 and a second request while rebuilding receives 409. The Library shows an admin-only "Rebuild index" action with a confirmation explaining that the current index remains available.
- [ ] `GET /index` returns `status` (`empty`, `updating`, `rebuilding`, `ready`, `ready_with_errors`, `failed`), `document_count`, `chunk_count`, `pending_document_count`, `updated_at`, `config_path`, `serving_build_id`, and the most recent `error`. An individual document failure produces `ready_with_errors` when a serving index still exists; `failed` means no usable index exists.
- [ ] The Library screen shows both the overall index status and each document's indexing status, polling every 3 seconds while any document is `pending`, `indexing`, or `deleting`, or while the index is `rebuilding`.
- [ ] When no serving index exists, Search and Ask show "The index is being prepared. Try again in a minute." An incremental failure does not disable Search or Ask for documents already indexed.
- [ ] An integration test indexes documents A and B, uploads document C, and proves A and B keep the same chunk ids and stored embeddings while C becomes retrievable. A second test deletes B and proves only B's chunks disappear.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-009: Search the shared corpus
**Description:** As a member, I want to search documents and see ranked passages so I can judge retrieval without waiting for an answer.

**Acceptance Criteria:**
- [ ] `POST /retrieval` accepts `{"query": "<text>"}` and calls the US-001 `retrieve` method. It never calls the generation model.
- [ ] The API records an interaction (`interaction_id`, `kind: "retrieval"`, user, query, `build_id`, champion config path, created time) and a snapshot of every hit: `rank` (from 1), `doc_id`, `text`, `score`.
- [ ] The request is traced with MLflow tracing in experiment `deepfix_product`. The trace id is stored on the interaction and the `interaction_id` is set as a trace tag.
- [ ] The response returns `interaction_id` and the hits, each with `rank`, `doc_id`, `document_filename`, `text`, `score`, and the current user's vote (`up`, `down`, or `null`).
- [ ] The number of hits follows `retrieval.top_k` (or `rerank.top_n` when reranking is enabled) on the champion config.
- [ ] Empty query returns 400. No ready index returns 503.
- [ ] The Search screen has one query field, a Search button, and a result list showing rank, filename, score, and text.
- [ ] No hits: "No passages matched "<query>"."
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-010: Ask a question
**Description:** As a member, I want to ask a question and read an answer grounded in the shared corpus.

**Acceptance Criteria:**
- [ ] `POST /ask` accepts `{"query": "<text>"}` and calls the US-001 `answer` method.
- [ ] The API records an interaction with `kind: "ask"`, the generated answer, and the hit snapshot exactly as in US-009, and traces the request in MLflow the same way.
- [ ] The response returns `interaction_id`, `answer`, the current user's answer vote, and the hits in the US-009 shape.
- [ ] The Ask screen shows the answer first and the passages it used underneath.
- [ ] If generation fails after retrieval succeeded, the response is HTTP 200 with `answer: null` and an `error` message, and the screen shows the error above the passages.
- [ ] Each ask is a single turn. No earlier question is sent as conversation memory.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-011: Vote on passages and answers
**Description:** As a member, I want to mark passages and answers as good or bad, and trust that my vote was kept.

**Acceptance Criteria:**
- [ ] Every passage on Search and Ask, and every answer on Ask, shows Thumbs up and Thumbs down.
- [ ] `POST /feedback` accepts `interaction_id`, `target` (`"passage"` with `rank`, or `"answer"`), and `action` (`"up"`, `"down"`, `"clear"`).
- [ ] A passage vote is keyed by (user, interaction, rank) and refers to the snapshotted `doc_id` and text for that rank, so it stays valid after the index is rebuilt.
- [ ] Votes are stored as an append-only event log. A user's current vote on a target is their latest event. `"clear"` removes the current vote without deleting history.
- [ ] The API returns 200 as soon as the event is committed to the product database. It does not wait for MLflow.
- [ ] An outbox worker mirrors each event to MLflow with `mlflow.log_feedback` on the interaction's trace: name `passage_relevance` (with `doc_id` and `rank` in metadata) or `answer_quality`, boolean value, source type `HUMAN`, source id = user id. A change updates the existing assessment. A clear removes it.
- [ ] Unsynced events are retried with backoff. Events unsynced for more than 5 minutes are counted in `GET /admin/feedback/summary`.
- [ ] The filled thumb shows the current vote after a page refresh. Pressing the filled thumb sends `"clear"`. Pressing the other thumb switches the vote.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-012: Build the feedback evaluation dataset
**Description:** As an AI engineer, I want votes turned into a `dev`/`holdout` dataset that can fairly score any candidate config.

**Acceptance Criteria:**
- [ ] The export groups interactions by normalized query text (Unicode NFKC, trimmed, lowercased, whitespace collapsed). One normalized query is one eval question with `query_id = "fb_" + first 12 hex chars of sha256(normalized query)`.
- [ ] For each question and `doc_id`, the export counts current passage up-votes and down-votes across all users and interactions. A non-deleted document is relevant when up-votes exceed down-votes, and negative when down-votes exceed up-votes. A tie, or a document with no votes, is unlabeled. A deleted document is dropped from both lists.
- [ ] A question is included when it has at least one relevant document or at least one negative document. A question whose judged documents were all deleted is excluded.
- [ ] Rows are written as `{"query_id", "query", "doc_ids", "negative_doc_ids"}` with no `answer` field. Both document lists are always present, and either may be empty. The champion's own answer text and its unvoted top hit are never used as labels.
- [ ] A question goes to `holdout` when `int(sha256(normalized query), 16) % 100 < 20`, otherwise to `dev`. The split is stable as new votes arrive.
- [ ] `dev` is written under `/workspace/feedback/<snapshot_id>/feedback_dev.jsonl`. `holdout` is written only under the API-private data directory, outside `/workspace`.
- [ ] Each export is logged as an MLflow run in experiment `deepfix_product_feedback` with both files as artifacts and parameters `dev_count`, `holdout_count`, `snapshot_id`.
- [ ] Answer votes are not eval labels. They are reported in `GET /admin/feedback/summary` as answer approval rate (up / (up + down)) overall and per champion config, and the list of down-voted answers is attached to the export run as `answer_downvotes.jsonl` for diagnosis.
- [ ] `GET /admin/feedback/summary` (admin only) returns vote counts by target and polarity, labeled `dev` and `holdout` question counts, answer approval rate, unsynced event count, and the MLflow experiment URLs. Members receive 403.
- [ ] Unit tests cover relevant-versus-negative aggregation, ties left unlabeled, deleted-document exclusion, inclusion of a negative-only question, exclusion of a question with no remaining labels, and split stability.

### US-013: Workspace layout for the embedded agent
**Description:** As a developer, I need the embedded Hermes agent to reach the corpus, the champion config, the `dev` dataset, and the `deepfix` CLI, without the holdout path being part of its prompt.

**Acceptance Criteria:**
- [ ] The API process uses one workspace directory, `/workspace` in Compose and a `tmp_path` equivalent in tests, containing `corpus/`, `indexes/`, `feedback/`, `configs/`, and `runs/`.
- [ ] Holdout files and the product database live in an API-private directory outside `/workspace`.
- [ ] The champion config lives at `/workspace/configs/champion.yaml`. Only API code writes it. It is seeded from `configs/default.yaml` on first start.
- [ ] The same environment installs the CLI with `uv tool install` from this repository, pinned to the git revision the API is running, so `deepfix` and `deepfix-rag` are on `PATH`. `deepfix --help` succeeds before a run starts.
- [ ] The agent may write candidate configs and candidate indexes only under `/workspace/runs/<run_id>/`.
- [ ] There is no second Hermes container and no `DEEPFIX_INTERNAL_TOKEN`. The Compose `hermes` gateway is not on the optimization path.

### US-014: Optimization run lifecycle owned by the API
**Description:** As an AI engineer, I want every optimization run, scheduled or manual, to start, finish, and promote through the API, so run state is visible and promotion is checked against data Hermes cannot see.

**Acceptance Criteria:**
- [ ] Starting a run is a Python function in the API process, used by both the scheduler and `POST /admin/optimization/run`. It exports a fresh feedback snapshot (US-012) and decides whether to run:
  - If `dev` has fewer than 20 questions, or fewer than 10 `dev` questions were added since the last run that was not skipped, it records a run with status `skipped` and the counts, and does not construct an `AIAgent`.
  - If another run is `running`, the admin route returns 409.
  - Otherwise it records a `running` run and starts one background task. The task builds a new `AIAgent` (US-003) and calls `run_conversation` with the fixed optimization prompt, the `dev` dataset path, the champion config path, the champion index directory, and the corpus path.
- [ ] The prompt tells the agent to follow `.agents/skills/optimization-loop/SKILL.md` by running `deepfix ... --json`, and to write candidates under `/workspace/runs/<run_id>/`. It does not include the holdout path.
- [ ] When `run_conversation` returns, API code reads the candidate written under that run directory. It does not ask the agent to promote itself. The API rejects the candidate (status `rejected`, reason recorded) if any of these fail:
  - The candidate validates as a `RAGConfig`, and `retrieval.backend` is `dense`, `hybrid`, or `bm25`.
  - On the submitted `dev` comparison, `delta.<primary_metric> >= 0.025` and no other metric in `delta` is negative (the existing quality gate).
  - `holdout` has at least 5 questions. The API itself builds a candidate index when index-time keys changed, runs `deepfix eval --retrieval-only` for candidate and champion on `holdout`, and requires `delta.<primary_metric> >= 0` for retrieval metrics.
- [ ] If all checks pass and only query-time keys changed, the API archives the previous champion, atomically replaces `/workspace/configs/champion.yaml`, reloads US-001 against the current index, and marks the run `promoted`.
- [ ] If an index-time key changed, the API first performs the US-008 full blue-green rebuild with the candidate. Only after that succeeds does it atomically switch both the serving-index pointer and `/workspace/configs/champion.yaml`. If it fails, the old index and champion continue serving and the run is marked `failed`.
- [ ] If the agent finishes without writing a candidate, the API marks the run `succeeded` when `final_response` reports no change, or `failed` with the agent error text otherwise. The MLflow run id from the agent's `deepfix eval` output is stored when present.
- [ ] A run still `running` 3 hours after it started is marked `failed` with reason "Timed out", which unblocks the next run.
- [ ] Integration tests exercise skip, conflict, rejection, and promotion by calling the lifecycle functions with `tmp_path` workspaces and the sample corpus using `--retrieval-only`. Those tests do not construct an `AIAgent`. No mocks of the pipeline or eval runner.

### US-015: Admin view of the Hermes optimization cron
**Description:** As an admin, I want to see the scheduled optimization job and recent runs so I know what Hermes is doing to the pipeline.

**Acceptance Criteria:**
- [ ] The Optimization screen is an admin-only route. Members who open the URL see "This page is only available to admins." and no controls.
- [ ] On API startup, the process starts an in-process scheduler for the job `deepfix-optimize-workspace`. The default schedule is `0 2 * * *` in `Europe/Berlin` (02:00 every day). The scheduled callback is the same function as Run now.
- [ ] `GET /admin/optimization` returns the cron expression, timezone, enabled flag, and next run time computed by that scheduler, plus the 20 most recent runs from the API's own run table: status (`running`, `skipped`, `rejected`, `promoted`, `succeeded`, `failed`), trigger (`cron` or `manual`), start and end times, reason, primary metric, `dev` and `holdout` deltas, and MLflow link.
- [ ] If `run_agent` cannot be imported or the LLM settings are missing, the response still returns the schedule and run history, with `agent_available: false`. The screen shows "Hermes is not available in this API process, so a run cannot start. The schedule and history are still shown." and disables Run now.
- [ ] The screen shows the current champion config path and a diff against the previous champion (from `configs/history/`), rendered from `deepfix diff-config` JSON.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-016: Run the optimization job now
**Description:** As an admin, I want to start the same Hermes job on demand instead of waiting for the nightly run.

**Acceptance Criteria:**
- [ ] `POST /admin/optimization/run` starts the same in-process job as the scheduler, with trigger `manual`. It does not submit a different prompt and does not call the Hermes gateway.
- [ ] The button label is "Run now". It is disabled while any run is `running`, with the note "A run is already in progress."
- [ ] Members receive 403. An admin request while a run is `running` receives 409. If the agent cannot be constructed, the route returns 503.
- [ ] A manual run that is skipped shows the reason with the actual counts, for example "Skipped: 14 labeled questions in dev, 20 needed."
- [ ] The screen polls `GET /admin/optimization` every 5 seconds while a run is `running`.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-017: Change the cron schedule
**Description:** As an admin, I want to change when the nightly job runs, or pause it.

**Acceptance Criteria:**
- [ ] The Optimization screen shows the cron expression in an editable field, an Enabled toggle, and a Save button.
- [ ] `PATCH /admin/optimization` accepts `{"cron": "<five-field cron>", "enabled": true | false}` and updates the in-process scheduler. The new schedule is stored in the product database and survives a restart.
- [ ] An invalid cron expression returns 400 and leaves the schedule unchanged. The field shows the error below it.
- [ ] Disabling the job stops scheduled runs. "Run now" still works while the job is disabled.
- [ ] After saving, the screen shows the new expression and the next run time computed by the scheduler.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

## Functional Requirements

- FR-1: The system must expose the product through a FastAPI app and a TanStack Start frontend. The CLI remains usable and unchanged in behavior for benchmark work.
- FR-1a: FastAPI's generated OpenAPI document is the only API contract. `@hey-api/openapi-ts` generates the TypeScript client from that document. Screens call the generated SDK and do not hand-write request types or `fetch` calls. The document has no `/internal` paths.
- FR-2: The domain layer must provide a long-lived query service that loads an index once and returns passages for retrieval and passages plus the generated answer for RAG.
- FR-3: `deepfix eval` must accept multiple relevant `doc_ids`, explicit `negative_doc_ids`, and questions without a reference answer. On feedback rows, retrieving a negative document lowers precision and nDCG, unjudged documents are skipped, and a row with negatives but no relevant documents is not treated as unanswerable. Benchmark rows that omit `negative_doc_ids` keep today's scoring. Reference-based metrics are skipped for rows without an answer.
- FR-4: The system must support exactly one shared corpus and one serving index for all signed-in users.
- FR-5: The system must authenticate users with email and password, assign role `admin` only to `DEEPFIX_BOOTSTRAP_ADMIN_EMAIL` or by admin promotion, and restrict all optimization and admin routes to admins.
- FR-6: The system must accept `.txt`, `.md`, and `.pdf` uploads up to 20 MB, extract text with UTF-8 decoding or `pypdf`, and assign `ws_<uuid>` document ids.
- FR-7: The system must index and delete one document at a time through a serialized writer queue without reprocessing unrelated documents. Full blue-green rebuilds occur only for initial creation, index-time configuration changes, or explicit repair, and switch into service only on success.
- FR-8: Workspace indexes must use `dense`, `hybrid`, or `bm25` retrieval. `graph` and `mixed` are rejected for the workspace.
- FR-9: The system must snapshot every hit (rank, `doc_id`, text, score) and every answer per interaction, and trace each Search and Ask request in MLflow experiment `deepfix_product`.
- FR-10: The system must store votes as an append-only event log keyed by user and target, confirm a vote once it is committed locally, and mirror it to MLflow with `mlflow.log_feedback` through a retrying outbox.
- FR-11: The system must derive eval labels only from passage votes. More up-votes than down-votes means relevant. More down-votes than up-votes means negative. Ties, unvoted documents, deleted documents, and unvoted champion output are not labels. A question is exported when it has at least one relevant or negative document.
- FR-12: The system must split feedback questions deterministically by query hash into `dev` (80%) and `holdout` (20%), expose only `dev` to Hermes, and log each snapshot to MLflow experiment `deepfix_product_feedback`.
- FR-13: The system must report answer votes as an approval rate per champion config, not as eval labels.
- FR-14: The workspace directory contains the corpus, indexes, `dev` feedback, champion config, and run directories. The holdout set and product database live outside that directory. The embedded agent is started with the workspace as its working directory.
- FR-15: Every optimization run, cron or manual, starts through one in-process function. It skips the run when `dev` has fewer than 20 questions or fewer than 10 new questions since the last non-skipped run, and the admin route returns 409 when a run is active. A run that starts constructs a new `AIAgent` and calls `run_conversation`.
- FR-16: The API must promote a candidate only when it validates, uses an allowed backend, passes the existing `dev` quality gate (primary delta ≥ 0.025, no negative secondary delta), and does not regress the primary retrieval metric on `holdout` (at least 5 questions). The agent does not apply this gate. Query-time changes reload against the current index; index-time changes require a successful blue-green rebuild before the champion and index switch together.
- FR-17: The API process must schedule one job, `deepfix-optimize-workspace`, default `0 2 * * *` in `Europe/Berlin`, and let admins trigger it, change its cron expression, and enable or disable it. The schedule is not a Hermes gateway cron job.
- FR-18: Runs still `running` after 3 hours must be marked `failed`.
- FR-19: Secrets (LLM keys, MLflow credentials) must stay on the server. The browser never receives them, and it never talks to Hermes directly.

## Non-Goals

- Multiple workspaces, per-user corpora, or collection pickers. There is one shared corpus.
- Microsoft Office uploads, images, audio, HTML archives, and OCR for scanned PDFs.
- Bulk import orchestration for very large corpora. Normal uploads and deletes are incremental per document; full rebuilds remain available for configuration migration and repair.
- `graph` and `mixed` retrieval backends for the workspace index.
- Multi-turn chat memory, suggested questions, and user-written corrected answers.
- Using answer votes or champion answers as reference answers for answer correctness.
- Graded relevance beyond binary labels. A passage is relevant, negative, or unlabeled.
- A custom optimizer inside FastAPI. The embedded Hermes agent forms the hypothesis and runs `deepfix`. API code only applies the quality gate and the holdout check.
- Driving optimization through the Hermes gateway HTTP API (ports 8642 and 9119) or through Hermes gateway cron.
- Launching Optuna HPO (`deepfix hpo`) from the UI.
- Editing YAML configs by hand in the UI.
- A hand-written OpenAPI file, or TypeScript API types maintained separately from FastAPI's schema.
- Public self-serve SaaS, billing, SSO, password-reset email, and audit-log export.
- Replacing the benchmark `dev`/`test` isolation used by research evals. Product feedback lives in separate MLflow experiments.
- Training or fine-tuning embedding or generation model weights.

## Design Considerations

The app is a small internal product with three member destinations and one admin destination.

| Screen | Who | Purpose |
| :--- | :--- | :--- |
| Sign in / Create account | Everyone | Email and password. Errors appear under the field they belong to. |
| Library | Members, admins | Upload, see shared documents and index status, delete own files (admins: any file). |
| Search | Members, admins | One query, ranked passages, thumbs on each passage. |
| Ask | Members, admins | One question, the answer, the passages it used, thumbs on the answer and each passage. |
| Optimization | Admins | Schedule, Enabled toggle, next run, Run now, recent runs with deltas, champion diff. |

- Search and Ask are separate screens. Search never generates an answer in the background.
- Thumbs up and thumbs down are always visible on each result, not hidden in a menu, and each has an accessible label ("Mark passage as relevant", "Mark passage as not relevant", "Mark answer as good", "Mark answer as bad").
- The filled thumb shows the current vote. Pressing it again clears the vote. Pressing the other thumb switches it.
- The Admin link appears only for admins.
- Empty, updating, rebuilding, agent-unavailable, skipped, and rejected states each use one plain sentence saying what happened and what to do next.
- Labels stay short: "Library", "Search", "Ask", "Optimization", "Run now". No marketing landing page.

## Technical Considerations

- **Layers.** Domain code (US-001 service, US-002 eval changes, feedback export, run lifecycle rules) lives in `deepfix_rag` modules and is tested without HTTP. `src/deepfix_rag/api/` only parses, calls, and serializes, mirroring the CLI rule in `AGENTS.md`.
- **Frontend.** The TanStack Start app lives in `web/`. It talks to the API only through the client `@hey-api/openapi-ts` generates from `web/openapi.json`, which is FastAPI's own schema (`app.openapi()`, also served at `GET /openapi.json`). The generator config is `web/openapi-ts.config.ts`: input `./openapi.json`, output `./src/client`, plugins `@hey-api/typescript`, `@hey-api/sdk`, and `@hey-api/client-fetch`. `web/src/api.ts` sets the base URL and `credentials: 'include'`. CORS allows only the web origin. Regenerating the client is part of CI, so a Pydantic model change that is not exported fails the build.
- **Product database.** SQLite on the API-private volume holds users, sessions, documents, interactions, hit snapshots, vote events, the MLflow outbox, index builds, and optimization runs. It is the source of truth for product state. MLflow holds traces, human feedback assessments, and dataset snapshots.
- **MLflow.** The Compose server runs MLflow 3.15, which supports tracing and `mlflow.log_feedback`. Each interaction's trace id is stored so feedback attaches to the exact request it judges. Experiments: `deepfix_product` (traces and feedback), `deepfix_product_feedback` (dataset snapshots). Benchmark experiments are untouched.
- **Label policy.** Passage votes are the only labels. The majority side wins: up is relevant, down is negative, and a tie is unlabeled. The champion's unvoted top hit and its answer text are never labels, because a label taken from champion output makes every other config look worse by construction. Feedback rows always include `negative_doc_ids`, so precision and nDCG punish retrieved negatives and ignore unjudged hits.
- **Incremental indexing.** Upload and delete are per-document mutations against the current LanceDB index through one writer queue. The implementation must add domain methods for upsert-by-`doc_id` and delete-by-`doc_id`; the current `_build_index()` whole-corpus path is retained for initial creation, configuration migrations, and repair. BM25 is reconstructed from the vector-store nodes after a mutation, because it is currently built in memory.
- **Promotion and index-time keys.** Query-time changes (for example `top_k`, fusion weights, reranking, and generation settings) reload the query service without rebuilding. Index-time changes (chunking, embedding, vector store, or backend) build a complete candidate index beside the serving index. The API switches the config and index together only after that build passes, so they never mismatch.
- **Holdout isolation.** The agent prompt and its working directory expose `dev` only. Holdout stays outside `/workspace`, and API code scores it after `run_conversation` returns. A terminal-enabled agent shares the API user, so this is a procedure boundary: the path is never given to the agent, and the agent cannot promote itself.
- **Hermes.** Optimization imports `AIAgent` from `run_agent` in the API process, as described in the [Python library guide](https://hermes-agent.nousresearch.com/docs/guides/python-library#basic-usage). Each run gets a new agent (`quiet_mode=True`, `skip_memory=True`, `max_iterations=15`) because instances are not thread-safe to share. Hermes is not installed from PyPI; the server uses a pinned checkout of `NousResearch/hermes-agent` on `PYTHONPATH`. The agent shells out to `deepfix`, installed on the same machine with `uv tool install`. The nightly schedule is an in-process scheduler, not the Hermes gateway.
- **Run ownership.** Run history, skip decisions, timeouts, and promotions live in the API's run table.
- **Performance targets (pilot VM, corpus up to 2,000 documents).** `POST /retrieval` p95 ≤ 2 s with `top_k ≤ 20` and reranking off. `POST /ask` p95 ≤ 15 s. `POST /feedback` p95 ≤ 200 ms, since it only writes locally.
- **Tests.** API tests use the new `api` marker. Indexing and query tests use `sample_corpus_path`, `sample_qa_path`, and `populated_sample_index_dir`. Eval tests use `--retrieval-only`. Temporary SQLite files and a temporary local MLflow tracking directory are allowed. No mocks of domain logic, per `AGENTS.md`.

## Success Metrics

- A new member can register, upload a text or PDF file, and get a Search hit from that file within 2 minutes of the upload on a corpus under 500 documents.
- 100% of votes that return HTTP 200 appear as MLflow feedback on their trace within 5 minutes while MLflow is reachable.
- At least 20% of Ask interactions receive a vote on the answer or a passage during the pilot.
- Before the end of the pilot, at least one scheduled or manual run promotes a config that passes the `dev` gate and does not regress `holdout`.
- Answer approval rate for the champion after that promotion is at least as high as before it, measured over at least 30 answer votes on each side.
- Members get 403 from every admin route, and never see the Admin link.
- Search and Ask meet the performance targets in Technical Considerations.

## Open Questions

- Should a later version replace binary labels with graded relevance, for example weighting a document by how many votes it received?

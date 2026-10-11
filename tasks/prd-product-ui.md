# PRD: DeepFix Product UI

## Introduction

DeepFix today is a deterministic CLI (`deepfix`) plus an agent loop. Engineers load data, build a LanceDB index, run retrieval or full RAG, score results with DeepEval, and let Hermes optimize the pipeline. That is powerful for the team and invisible to everyone else.

This feature turns that pipeline into a small internal product. A FastAPI service wraps the existing domain layer and publishes its OpenAPI schema. A TanStack Start app is the interface. Its TypeScript client is generated from that schema with `@hey-api/openapi-ts`, so the screens never hand-write request types. Signed-in members share **one workspace corpus**: they upload text, Markdown, and PDF files, search the index, and ask questions. Thumbs up and thumbs down on retrieved passages and on answers are stored, traced in MLflow, and turned into an evaluation dataset. Admins, and only admins, open an Optimization section that shows the nightly schedule and can start that same job immediately. The job runs Hermes through its Python library in a child process that the API starts, watches, and can kill. It does not use the Hermes gateway.

The feedback loop is the point of the feature, so this PRD is strict about three things:

1. **Votes must become labels that can judge a different config.** Passage votes decide whether a document is relevant or negative for a question. Nothing the current champion produced on its own (its unvoted top hit, its answer text) is ever used as a label, because that would score every alternative config as worse than the champion.
2. **Optimization must not grade itself.** Feedback is split by question into a `dev` set Hermes optimizes on and a `holdout` set that never leaves the API process. A candidate is promoted only if it also holds up on `holdout`.
3. **The API owns run state and promotion.** Hermes proposes a candidate config. The API decides whether a run starts, re-scores the candidate itself on `dev` and `holdout`, decides whether it is promoted, and decides when the live index is rebuilt.

## Goals

- Give members a signed-in app where they can upload documents into one shared corpus, search it, and ask grounded questions.
- Record thumbs up and thumbs down on passages and answers, never lose a vote, and mirror every vote to MLflow as feedback on the trace of the request it judges.
- Turn passage votes into a `dev`/`holdout` evaluation dataset that can score any candidate config.
- Let admins inspect the nightly Hermes optimization job, change its schedule, and start it on demand.
- Promote a config only when the API's own re-score shows it beats the champion on `dev` by the quality gate and does not regress on `holdout`.
- Keep product API code thin: FastAPI calls `deepfix_rag` domain functions. The Hermes agent calls the `deepfix` CLI for index, eval, and config changes.
- Generate the frontend API client from FastAPI's OpenAPI schema with `@hey-api/openapi-ts`. The schema is the contract.
- Ship a pilot that one team runs on one server: one API process (which launches Hermes runs as child processes), the web app, and Docker Compose for MLflow and the diagnostic server. Not a multi-tenant SaaS.

## User Stories

Stories are ordered by dependency. Implementation tickets live in `tasks/product-ui_issues/`:

| Story | Ticket | Story | Ticket |
| :--- | :--- | :--- | :--- |
| US-001 Query service | 01 | US-010 Ask | 09 |
| US-002 Multi-label eval records | 02 | US-011 Votes and MLflow outbox | 10, 11 |
| US-003 Hermes agent runner | 13 | US-012 Feedback dataset | 12 |
| US-004, US-005 API shell and generated client | 03 | US-013 Workspace layout | 05, 13 |
| US-006 Accounts and roles | 04 | US-014 Run lifecycle and promotion | 14, 15 |
| US-007 Upload and Library | 05 | US-015, US-016 Optimization screen and Run now | 16 |
| US-008 Shared index (full build, incremental) | 06, 08 | US-017 Cron editing | 17 |
| US-009 Search | 07 | | |

### US-001: Workspace query service in the domain layer
**Description:** As a developer, I need a long-lived service that loads an index once and answers retrieval and RAG requests, so the API does not reload the index on every request.

**Context:** `RAGWorkflow.synthesize` already returns a `RAGResult` with `retrieval_results` and `generated_answer`, and honors `RAGConfig.retrieval_only`. What is missing: `execute_pipeline` loads (or ingests) the index through `workflow.run(ingest=...)` on every call, and retrieval-only is a config flag, not a per-call option. `RAGResult.generated_answer` is still typed `Optional[str]`.

**Acceptance Criteria:**
- [ ] A new domain module (for example `src/deepfix_rag/service.py`) exposes a class that is constructed with a `RAGConfig` and an index directory, loads the existing index once, and keeps it in memory.
- [ ] `async retrieve(query: str) -> list[RetrievalResult]` runs retrieval and reranking only, regardless of `config.retrieval_only`, and never calls the generation model.
- [ ] `async answer(query: str) -> RAGResult` runs retrieval, reranking, and synthesis and returns both `retrieval_results` and `generated_answer`.
- [ ] `RAGResult.generated_answer` is annotated `str | None`, matching the repo typing rules.
- [ ] Every `RetrievalResult` keeps its parent `doc_id` (via `DataLoader.parent_doc_id`), score, and chunk text, in rank order.
- [ ] Calling `retrieve` twice on the same service does not rebuild or reload the index. A test asserts this without mocks by counting index loads through a real counter on the service.
- [ ] Existing CLI commands (`index`, `eval`) keep working unchanged.
- [ ] Tests use `populated_sample_index_dir` and `sample_rag_config`; no mocks of the pipeline.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-002: Multi-label, reference-optional eval records
**Description:** As an AI engineer, I need eval to accept several relevant documents, explicit negative documents, and questions without a reference answer, so product feedback can be scored without inventing labels.

**Context:** `QARecord` has one required `doc_id` and one required `answer`. The runner builds `ground_truth_doc_ids = [qa.doc_id]` and treats a record with no ground-truth documents as unanswerable.

**Acceptance Criteria:**
- [ ] `QARecord` gains `doc_ids: list[str]`, `negative_doc_ids: list[str] | None`, and makes `answer: str | None`. A record loaded with a single `doc_id` string produces `doc_ids=[doc_id]` and `negative_doc_ids=None`, so every existing benchmark file loads unchanged.
- [ ] JSONL rows may carry either `"doc_id": "<id>"` or `"doc_ids": ["<id>", ...]`. A row with both keys is rejected with exit code 1. The same `doc_id` in both `doc_ids` and `negative_doc_ids` is rejected with exit code 1.
- [ ] When `negative_doc_ids` is absent, scoring stays as it is today: `doc_ids` are relevant, and every retrieved document outside that list is non-relevant.
- [ ] When `negative_doc_ids` is present, labels are binary and judged-only. `doc_ids` are relevant (label 1). `negative_doc_ids` are negative (label 0). A retrieved document in neither list is unjudged: it is left out of precision and nDCG. Retrieving a negative document lowers precision and nDCG. Recall uses only `doc_ids`. A row with an empty `doc_ids` list omits recall and is not treated as unanswerable.
- [ ] For rows where `answer` is missing, metrics that require a reference answer (answer correctness and any DeepEval metric that needs `expected_output`, such as contextual precision and contextual recall) are skipped for that row and not averaged into the summary. Reference-free metrics (faithfulness, answer relevancy) still run.
- [ ] A metric skipped for every row is absent from `eval --compare` `delta`, and the optimization-loop skill already treats a missing delta as "not an improvement".
- [ ] Existing unanswerable handling is unchanged: a row is unanswerable only if it is explicitly marked so, not merely because `answer` is missing.
- [ ] The eval runner accepts records passed in memory (a `list[QARecord]`) as well as a dataset path, so the API can score `holdout` without writing it to disk (US-014). The comparison output has the same shape either way and includes per-question metric values.
- [ ] Unit tests cover single-`doc_id`, multi-`doc_ids`, missing-`answer`, both-keys rows, a retrieved negative lowering precision, an unjudged hit left out of precision, a negative-only row that is not unanswerable, an unchanged benchmark row that has no `negative_doc_ids`, and in-memory records scoring the same as the same rows loaded from a file.
- [ ] `uv run --env-file .env pytest -v -m "not network and not e2e"` passes.

### US-003: Hermes agent runner in a child process
**Description:** As a developer, I want each optimization run to execute Hermes through its Python library in a separate process, so a run can be time-limited and killed, a crash cannot take down the API, and Hermes dependencies stay out of the `deepfix_rag` environment.

**Context:** [Using Hermes as a Python library](https://hermes-agent.nousresearch.com/docs/guides/python-library#basic-usage) exposes `AIAgent` from `run_agent`. `run_conversation()` returns `final_response` and `messages`. Hermes does not publish a wheel: the runtime is a checkout of `https://github.com/NousResearch/hermes-agent.git`. An agent instance is not safe to share across runs. A Python thread cannot be killed, so an in-process agent cannot honor a timeout.

**Acceptance Criteria:**
- [ ] A pinned Hermes commit, the checkout path, and the interpreter of a dedicated Hermes virtual environment (`HERMES_PYTHON`) are recorded in `tasks/notes-hermes-integration.md`. That environment has Hermes and its dependencies and does not have `deepfix_rag` installed.
- [ ] A standalone runner script (for example `scripts/hermes_runner.py`, no `deepfix_rag` imports) runs under `HERMES_PYTHON`. It reads a job file (`runs/<run_id>/job.json`: prompt, model settings, iteration limit), builds one `AIAgent` with `quiet_mode=True`, `skip_memory=True`, and `max_iterations` from the job, calls `run_conversation(user_message=..., task_id=run_id)`, and writes `runs/<run_id>/agent_output.json` with `final_response` and an error, if any.
- [ ] A domain helper in `deepfix_rag` writes the job file, starts the runner as a child process with working directory `/workspace`, and waits for it. When the run exceeds `OPTIMIZATION_RUN_TIMEOUT_HOURS` (default 3), the helper kills the process group and reports a timeout.
- [ ] The child's environment is built from an explicit allowlist: LLM base URL, key, and model; `MLFLOW_TRACKING_URI`; `DEEPFIX_SERVER_URL`; and a `PATH` whose first entry is the API virtual environment's `bin/`, so `deepfix` resolves to the same code revision as the API. The product database path, the API-private data directory, and any session secret are not in the environment or the prompt.
- [ ] The agent's terminal tool is enabled. Indexing, eval, and config mutation happen only when the agent shells out to `deepfix`. The runner does not call the Hermes gateway on ports 8642 or 9119.
- [ ] A test, skipped only when `HERMES_PYTHON` is unset or cannot import `run_agent`, runs one agent turn whose prompt is to execute `deepfix --help` and asserts the command's usage text appears in `final_response`. A second test starts a runner that sleeps past a short timeout and asserts the process is gone afterwards.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-004: FastAPI service shell
**Description:** As a developer, I want a FastAPI app that boots beside the existing package so the product has an HTTP boundary around the domain code.

**Acceptance Criteria:**
- [x] The app lives in `src/deepfix_rag/api/` and starts with `uv run --env-file .env uvicorn deepfix_rag.api.app:app`. It runs with exactly one worker process (see Technical Considerations: Process model).
- [x] `GET /health` returns HTTP 200 and `{"status": "ok"}` without calling the LLM, embeddings, MLflow, or Hermes.
- [x] Route handlers parse input, call domain code (US-001 service, data loaders, eval runner), and serialize output. No retrieval, indexing, or evaluation logic lives in route functions.
- [x] Errors use `{"detail": "<message>"}` with 400 for bad input, 401 for no session, 403 for wrong role, 404 for unknown ids, 409 for conflicting state, and 503 when a required dependency (ready index, or the Hermes runtime) is unavailable. That body is a Pydantic model named in the route's `responses`, so it appears in the schema.
- [x] Every public route declares a Pydantic request body (or form fields) and a Pydantic response model. No public route returns an untyped `dict`.
- [x] Every public route sets an explicit `operation_id` in snake_case (`health`, `register`, `login`, and so on). FastAPI must not invent ids from function names.
- [x] The app has no `/internal` routes. Optimization calls Python functions in-process. `GET /openapi.json` lists only the public product routes, and a test asserts no path starts with `/internal`.
- [x] `uv run python -m deepfix_rag.api.export_openapi` writes that document to `web/openapi.json` by calling `app.openapi()`. It does not start a server and does not load an index.
- [x] An `api` pytest marker is registered in `pyproject.toml` and used for all API tests.
- [x] `uv run ruff check` and `uv run ruff format --check` pass on the API package.

### US-005: TypeScript client generated from the FastAPI schema
**Description:** As a frontend developer, I want the API client generated from FastAPI's OpenAPI schema so request and response types stay in lockstep with the Python models.

**Acceptance Criteria:**
- [x] `web/openapi-ts.config.ts` uses `@hey-api/openapi-ts` with `input: './openapi.json'` (the file exported in US-004) and `output: './src/client'`.
- [x] Plugins are `@hey-api/typescript`, `@hey-api/sdk`, and `@hey-api/client-fetch`. No second HTTP client plugin is enabled.
- [x] The generated fetch client is configured once, in `web/src/api.ts`, with the API base URL from the environment and `credentials: 'include'` so the session cookie is sent.
- [x] `npm run generate:api` in `web/` exports `web/openapi.json` and then runs `openapi-ts`. `web/src/client` is produced only by that command. Nobody edits files in that directory by hand.
- [x] CI regenerates the client and fails if `web/openapi.json` or `web/src/client` differs from what is committed.
- [x] Application code under `web/src` imports SDK functions from `web/src/client`. A check fails if any file outside `web/src/client` calls `fetch` against the API.
- [x] The generated SDK contains no function whose path starts with `/internal`.
- [x] Typecheck passes.

### US-006: Email and password accounts with roles
**Description:** As a member, I want an account so my uploads and votes are attributed to me. As an admin, I want optimization controls hidden from members.

**Acceptance Criteria:**
- [ ] The browser reaches the API only under the web origin at `/api`: the Vite dev server proxies `/api/*` to FastAPI, and the TanStack Start server does the same in production. The browser base URL in `web/src/api.ts` becomes `/api`. Server-side rendering calls the API at an internal URL (`API_INTERNAL_URL`) and forwards the incoming request's `Cookie` header, in one place in `web/src/api.ts`.
- [ ] `POST /auth/register` accepts `email` and `password` (minimum 8 characters) and creates a user with role `member`. When `ALLOWED_EMAIL_DOMAINS` is set (comma-separated), any other domain returns 400 with "Registration is limited to <domains> addresses."
- [ ] Registration never creates an admin. The first admin is created on the server with `uv run python -m deepfix_rag.api.manage create-admin --email <email>`, which reads the password from standard input. The command is not part of the `deepfix` CLI, so the optimization agent does not get it on its path.
- [ ] `POST /auth/login` sets an httpOnly, `SameSite=Lax` session cookie (`Secure` when served over HTTPS). `POST /auth/logout` clears it. `GET /auth/me` returns `id`, `email`, and `role`.
- [ ] Passwords are stored only as Argon2 hashes. No endpoint returns the hash.
- [ ] Sessions expire 14 days after creation.
- [ ] `PATCH /admin/users/{user_id}` with `{"role": "admin" | "member"}` changes a role. Members receive 403. Demoting the last remaining admin returns 409.
- [ ] Sign-in, sign-out, and register screens exist in the TanStack Start app. After sign-in, the header shows the user's email and shows an Admin link only when `role` is `admin`. Signed-out visitors to any other page are sent to sign in.
- [ ] Typecheck passes.
- [ ] Verify in browser.

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
- [ ] The Library screen lists filename, uploader email, status, and upload time, and offers Upload and, where allowed, Delete. It replaces the current mock "Data upload" screen.
- [ ] Empty state: "No documents yet. Upload a text, Markdown, or PDF file to start."
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-008: Maintain the shared index
**Description:** As a member, I want each uploaded document indexed independently so it becomes searchable without rebuilding unrelated documents.

**Acceptance Criteria:**
- [ ] The serving index normally lives at `<workspace>/indexes/current/`. Uploading one document creates a background job for that `doc_id`; it does not re-read, re-chunk, or re-embed any other document.
- [ ] Before indexing, the API writes the extracted source to `<workspace>/corpus/<doc_id>.json` with `{"doc_id", "text", "filename"}`. The corpus directory is the reproducible source for a full rebuild.
- [ ] `LocalFileLoader` accepts a directory as `data.path_or_repo` and loads every `*.json` file in it as one document. `deepfix index` and `deepfix eval` with `data.path_or_repo=<workspace>/corpus` therefore load the workspace corpus, for the API's full rebuild and for the agent's candidate indexes alike. JSONL files keep loading as today.
- [ ] The API owns the location fields of every workspace config. When it seeds, loads, or promotes a config, it overwrites `retrieval.lancedb_index_dir` with the serving or build directory and `data.path_or_repo` with `<workspace>/corpus`. Values in a candidate config for those fields are ignored.
- [ ] The per-document job chunks only that document with the active champion config, preserves parent `doc_id` on every chunk, embeds those chunks, and inserts them into the current LanceDB table.
- [ ] A document moves `pending` → `indexing` → `indexed`. On failure it moves to `failed`, stores `index_error`, and leaves all other documents and chunks unchanged. Retrying the document is idempotent: existing chunks for that `doc_id` are removed before its replacement chunks are inserted.
- [ ] Deleting a document moves it to `deleting`, removes every chunk whose parent metadata `doc_id` matches, removes `<workspace>/corpus/<doc_id>.json`, then sets `deleted_at`. A failed index deletion leaves the document visible as failed/deleting to admins and is retried; it is not reported as successfully deleted.
- [ ] Per-document mutations are serialized through one index-writer queue. Search and Ask continue reading the current index while a mutation runs. The service refreshes its BM25/hybrid lexical retriever after a successful insert or delete so lexical results include the change.
- [ ] On API startup, documents left in `indexing` are re-queued and documents left in `deleting` resume deletion, so a restart never leaves a document stuck.
- [ ] Workspace indexing supports `retrieval.backend` values `dense`, `hybrid`, and `bm25` only. A champion config with `graph` or `mixed` is rejected (see US-014), because per-document graph mutation and relation cleanup are out of scope.
- [ ] A **full blue-green rebuild** occurs only when there is no current index, when an index-time key changes, or when an admin explicitly requests repair. Index-time keys are `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, and `retrieval.bit_width`. The embedding model is the `EMBEDDING_MODEL` server setting, not a config key, so a candidate config cannot change it. The rebuild builds all non-deleted documents from `<workspace>/corpus/` into `<workspace>/indexes/builds/<build_id>/`.
- [ ] A full rebuild never writes into the serving directory. On success, the API atomically switches the current-index pointer and reloads US-001; requests already in flight finish on the old index. On failure, the old index and config keep serving. Superseded builds are deleted once no request uses them.
- [ ] `POST /admin/index/rebuild` starts that repair rebuild. Members receive 403 and a second request while rebuilding receives 409. The Library shows an admin-only "Rebuild index" action with a confirmation explaining that the current index remains available.
- [ ] `GET /index` returns `status` (`empty`, `updating`, `rebuilding`, `ready`, `ready_with_errors`, `failed`), `document_count`, `chunk_count`, `pending_document_count`, `updated_at`, `config_path`, `serving_build_id`, and the most recent `error`. An individual document failure produces `ready_with_errors` when a serving index still exists; `failed` means no usable index exists.
- [ ] The Library screen shows both the overall index status and each document's indexing status, polling every 3 seconds while any document is `pending`, `indexing`, or `deleting`, or while the index is `rebuilding`.
- [ ] When no serving index exists, Search and Ask show "The index is being prepared. Try again in a minute." An incremental failure does not disable Search or Ask for documents already indexed.
- [ ] An integration test indexes documents A and B, uploads document C, and proves A and B keep the same chunk ids and stored embeddings while C becomes retrievable. A second test deletes B and proves only B's chunks disappear.
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-009: Search the shared corpus
**Description:** As a member, I want to search documents and see ranked passages so I can judge retrieval without waiting for an answer.

**Acceptance Criteria:**
- [ ] `POST /retrieval` accepts `{"query": "<text>"}` and calls the US-001 `retrieve` method. It never calls the generation model.
- [ ] The API records an interaction (`interaction_id`, `kind: "retrieval"`, user, query, `build_id`, champion config path, created time) and a snapshot of every returned hit: `rank` (from 1), `doc_id`, `text`, `passage_key` (`sha256` of the chunk text), `score`.
- [ ] The request is traced with MLflow tracing in experiment `deepfix_product`. The trace id is stored on the interaction and the `interaction_id` is set as a trace tag. A tracing failure is logged and does not fail the request; the interaction is then stored without a trace id.
- [ ] The response returns `interaction_id` and the hits, each with `rank`, `doc_id`, `document_filename`, `text`, `score`, and the current user's vote (`up`, `down`, or `null`).
- [ ] The number of hits follows `retrieval.top_k` (or `rerank.top_n` when reranking is enabled) on the champion config, capped at `MAX_DISPLAYED_HITS` (default 10).
- [ ] Empty query returns 400. No ready index returns 503.
- [ ] The Search screen has one query field, a Search button, and a result list showing rank, filename, score, and text. It replaces the current mock Search screen.
- [ ] No hits: "No passages matched "<query>"."
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-010: Ask a question
**Description:** As a member, I want to ask a question and read an answer grounded in the shared corpus.

**Acceptance Criteria:**
- [ ] `POST /ask` accepts `{"query": "<text>"}` and calls the US-001 `answer` method.
- [ ] The API records an interaction with `kind: "ask"`, the generated answer, and the hit snapshot exactly as in US-009, and traces the request in MLflow the same way.
- [ ] The response returns `interaction_id`, `answer`, the current user's answer vote, and the hits in the US-009 shape.
- [ ] The Ask screen shows the answer first and the passages it used underneath. It replaces the current mock Chat screen.
- [ ] If generation fails after retrieval succeeded, the response is HTTP 200 with `answer: null` and an `error` message, and the screen shows the error above the passages.
- [ ] Each ask is a single turn. No earlier question is sent as conversation memory.
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-011: Vote on passages and answers
**Description:** As a member, I want to mark passages and answers as good or bad, and trust that my vote was kept.

**Acceptance Criteria:**
- [ ] Every passage on Search and Ask, and every answer on Ask, shows Thumbs up and Thumbs down.
- [ ] `POST /feedback` accepts `interaction_id`, `target` (`"passage"` with `rank`, or `"answer"`), and `action` (`"up"`, `"down"`, `"clear"`).
- [ ] A passage vote is keyed by (user, interaction, rank) and refers to the snapshotted `doc_id`, `passage_key`, and text for that rank, so it stays valid after the index is rebuilt.
- [ ] Votes are stored as an append-only event log. A user's current vote on a target is their latest event. `"clear"` removes the current vote without deleting history.
- [ ] The API returns 200 as soon as the event is committed to the product database. It does not wait for MLflow.
- [ ] An outbox worker mirrors each event to MLflow with `mlflow.log_feedback` on the interaction's trace: name `passage_relevance` (with `doc_id` and `rank` in metadata) or `answer_quality`, boolean value, source type `HUMAN`, source id = user id. A change updates the existing assessment. A clear removes it.
- [ ] Unsynced events are retried with backoff. An event whose interaction has no trace id, or whose trace MLflow reports as missing, is marked `no_trace` and not retried; the vote still counts locally. Events unsynced for more than 5 minutes, and `no_trace` events, are counted separately in `GET /admin/feedback/summary`.
- [ ] The filled thumb shows the current vote after a page refresh. Pressing the filled thumb sends `"clear"`. Pressing the other thumb switches the vote.
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-012: Build the feedback evaluation dataset
**Description:** As an AI engineer, I want votes turned into a `dev`/`holdout` dataset that can fairly score any candidate config.

**Acceptance Criteria:**
- [ ] The export groups interactions by normalized query text (Unicode NFKC, trimmed, lowercased, whitespace collapsed). One normalized query is one eval question with `query_id = "fb_" + first 12 hex chars of sha256(normalized query)`.
- [ ] Votes are first resolved per passage: for each question and `passage_key`, the net vote is current up-votes minus current down-votes across all users and interactions. A passage is up when its net vote is positive and down when it is negative.
- [ ] Labels are per document, because eval scores parent documents. For a question, a non-deleted document is relevant when at least one of its passages is up. It is negative when none of its passages is up and at least one is down. Otherwise it is unlabeled. A down-vote on one passage therefore never cancels an up-vote on a different passage of the same document. A deleted document is dropped from both lists.
- [ ] A question is included when it has at least one relevant document or at least one negative document. A question whose judged documents were all deleted is excluded.
- [ ] Rows have the shape `{"query_id", "query", "doc_ids", "negative_doc_ids"}` with no `answer` field. Both document lists are always present, and either may be empty. The champion's own answer text and its unvoted top hit are never used as labels.
- [ ] A question goes to `holdout` when `int(sha256(normalized query), 16) % 100 < 20`, otherwise to `dev`. The split is stable as new votes arrive.
- [ ] `dev` is written to `/workspace/feedback/<snapshot_id>/feedback_dev.jsonl`. `holdout` is never written to disk or to MLflow. The export returns it as in-memory records for the promotion gate (US-014) and is recomputed from the database whenever it is needed.
- [ ] Each export is logged as an MLflow run in experiment `deepfix_product_feedback` with the `dev` file as an artifact and parameters `dev_count`, `holdout_count`, `snapshot_id`, and `holdout_ids_sha256` (a hash of the sorted holdout query ids, so a snapshot can be audited without revealing it).
- [ ] Answer votes are not eval labels. They are reported in `GET /admin/feedback/summary` as answer approval rate (up / (up + down)) overall and per champion config, and the list of down-voted answers is attached to the export run as `answer_downvotes.jsonl` for diagnosis.
- [ ] `GET /admin/feedback/summary` (admin only) returns vote counts by target and polarity, labeled `dev` and `holdout` question counts, answer approval rate, unsynced and `no_trace` event counts, and the MLflow experiment URLs. Members receive 403.
- [ ] Unit tests cover per-passage net votes, a down-voted passage not cancelling an up-voted passage of the same document, a negative-only document, ties left unlabeled, deleted-document exclusion, inclusion of a negative-only question, exclusion of a question with no remaining labels, split stability, and that no export path writes holdout rows to a file.

### US-013: Workspace layout for the agent
**Description:** As a developer, I need the Hermes agent to reach the corpus, the champion config, the `dev` dataset, and the `deepfix` CLI, and nothing that would let it see `holdout` or product state.

**Acceptance Criteria:**
- [ ] The API process uses one workspace directory, `/workspace` in Compose and a `tmp_path` equivalent in tests, containing `corpus/`, `indexes/`, `feedback/`, `configs/`, and `runs/`.
- [ ] The product database and session secrets live in an API-private directory outside `/workspace`. No holdout file exists anywhere (US-012).
- [ ] The champion config lives at `/workspace/configs/champion.yaml`. Only API code writes it. It is seeded from `configs/default.yaml` on first start, with the location fields set as in US-008. Previous champions are archived under `/workspace/configs/history/`.
- [ ] The agent may write candidate configs and candidate indexes only under `/workspace/runs/<run_id>/`.
- [ ] There is no second Hermes container and no `DEEPFIX_INTERNAL_TOKEN`. The Compose `hermes` gateway is not on the optimization path.
- [ ] In Compose, the agent child process runs as a separate unprivileged user that can write `/workspace` and cannot read the API-private directory. Outside Compose (local development), this is a documented procedure boundary: the agent is never told the path and cannot promote itself.

### US-014: Optimization run lifecycle owned by the API
**Description:** As an AI engineer, I want every optimization run, scheduled or manual, to start, finish, and promote through the API, so run state is visible and promotion is checked against data Hermes cannot see.

**Acceptance Criteria:**
- [ ] Gate thresholds are server settings: `OPTIMIZATION_MIN_DEV_QUESTIONS` (default 50), `OPTIMIZATION_MIN_NEW_DEV_QUESTIONS` (default 10), `OPTIMIZATION_MIN_HOLDOUT_QUESTIONS` (default 15), `OPTIMIZATION_MIN_PRIMARY_DELTA` (default 0.025), and `OPTIMIZATION_RUN_TIMEOUT_HOURS` (default 3).
- [ ] Starting a run is one Python function in the API process, used by both the scheduler and `POST /admin/optimization/run`. It exports a fresh feedback snapshot (US-012) and decides whether to run:
  - If `dev` has fewer than the minimum questions, fewer than the minimum new `dev` questions since the last run that was not skipped, or `holdout` has fewer than its minimum, it records a run with status `skipped` and the counts, and does not start the agent.
  - If another run is `running`, the admin route returns 409.
  - Otherwise it records a `running` run and starts one background task that launches the US-003 runner with the fixed optimization prompt, the `dev` dataset path, the champion config path, the champion index directory, and the corpus path.
- [ ] The prompt tells the agent to follow `.agents/skills/optimization-loop/SKILL.md` by running `deepfix ... --json`, to use only `dense`, `hybrid`, or `bm25`, to write candidates under `/workspace/runs/<run_id>/`, and to finish by writing `/workspace/runs/<run_id>/result.json`: `{"candidate_config_path": "<path>" | null, "primary_metric": "<metric>", "summary": "<text>", "mlflow_run_id": "<id>" | null}`.
- [ ] When the runner exits, API code reads `result.json` and validates it against a Pydantic model. A missing or invalid file marks the run `failed` with the runner error. `candidate_config_path: null` marks the run `succeeded` with the summary as reason. The API does not parse `final_response` to decide the outcome, and it does not ask the agent to promote itself.
- [ ] For a candidate, the API rejects it (status `rejected`, reason recorded) unless all of these hold:
  - The candidate validates as a `RAGConfig`, and `retrieval.backend` is `dense`, `hybrid`, or `bm25`.
  - The API itself scores candidate and champion on the snapshot's `dev` set. Numbers reported by the agent are stored for reference only. `delta.<primary_metric>` must be at least `OPTIMIZATION_MIN_PRIMARY_DELTA`, and no other metric in `delta` may be negative.
  - The API scores candidate and champion on `holdout` in memory (US-002) with retrieval-only eval, building a candidate index first when index-time keys changed. `delta.<primary_metric>` must be at least 0, and the candidate must win at least as many holdout questions as it loses on that metric.
- [ ] If all checks pass and only query-time keys changed, the API archives the previous champion, atomically replaces `/workspace/configs/champion.yaml`, reloads US-001 against the current index, and marks the run `promoted`.
- [ ] If an index-time key changed, the API first performs the US-008 full blue-green rebuild with the candidate. Only after that succeeds does it atomically switch both the serving-index pointer and `/workspace/configs/champion.yaml`. If it fails, the old index and champion continue serving and the run is marked `failed`.
- [ ] A run that exceeds the timeout is killed (US-003) and marked `failed` with reason "Timed out", which unblocks the next run. On API startup, any run still marked `running` is marked `failed` with reason "Interrupted by restart".
- [ ] Integration tests exercise skip, conflict, rejection, and promotion by calling the lifecycle functions with `tmp_path` workspaces, the sample corpus, and a hand-written `result.json`, using `--retrieval-only`. Those tests do not start the agent. No mocks of the pipeline or eval runner.

### US-015: Admin view of the optimization schedule
**Description:** As an admin, I want to see the scheduled optimization job and recent runs so I know what Hermes is doing to the pipeline.

**Acceptance Criteria:**
- [ ] The Optimization screen is an admin-only route. It replaces the current mock Admin metrics screen. Members who open the URL see "This page is only available to admins." and no controls.
- [ ] On API startup, the process starts an in-process scheduler for the job `deepfix-optimize-workspace`. The default schedule is `0 2 * * *` in `Europe/Berlin` (02:00 every day). The scheduled callback is the same function as Run now.
- [ ] `GET /admin/optimization` returns the cron expression, timezone, enabled flag, and next run time computed by that scheduler, plus the 20 most recent runs from the API's own run table: status (`running`, `skipped`, `rejected`, `promoted`, `succeeded`, `failed`), trigger (`cron` or `manual`), start and end times, reason, primary metric, `dev` and `holdout` deltas, holdout wins and losses, and MLflow link.
- [ ] If `HERMES_PYTHON` is unset, cannot import `run_agent`, or the LLM settings are missing, the response still returns the schedule and run history, with `agent_available: false`. The screen shows "Hermes is not set up on this server, so a run cannot start. The schedule and history are still shown." and disables Run now.
- [ ] The screen shows the current champion config path and a diff against the previous champion (from `configs/history/`), rendered from `deepfix diff-config` JSON.
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-016: Run the optimization job now
**Description:** As an admin, I want to start the same Hermes job on demand instead of waiting for the nightly run.

**Acceptance Criteria:**
- [ ] `POST /admin/optimization/run` starts the same job as the scheduler, with trigger `manual`. It does not submit a different prompt and does not call the Hermes gateway.
- [ ] The button label is "Run now". It is disabled while any run is `running`, with the note "A run is already in progress."
- [ ] Members receive 403. An admin request while a run is `running` receives 409. If the agent is not available, the route returns 503.
- [ ] A manual run that is skipped shows the reason with the actual counts, for example "Skipped: 14 labeled questions in dev, 50 needed."
- [ ] The screen polls `GET /admin/optimization` every 5 seconds while a run is `running`.
- [ ] Typecheck passes.
- [ ] Verify in browser.

### US-017: Change the cron schedule
**Description:** As an admin, I want to change when the nightly job runs, or pause it.

**Acceptance Criteria:**
- [ ] The Optimization screen shows the cron expression in an editable field, an Enabled toggle, and a Save button.
- [ ] `PATCH /admin/optimization` accepts `{"cron": "<five-field cron>", "enabled": true | false}` and updates the in-process scheduler. The new schedule is stored in the product database and survives a restart.
- [ ] An invalid cron expression returns 400 and leaves the schedule unchanged. The field shows the error below it.
- [ ] Disabling the job stops scheduled runs. "Run now" still works while the job is disabled.
- [ ] After saving, the screen shows the new expression and the next run time computed by the scheduler.
- [ ] Typecheck passes.
- [ ] Verify in browser.

## Functional Requirements

- FR-1: The system must expose the product through a FastAPI app and a TanStack Start frontend. The CLI remains usable and unchanged in behavior for benchmark work.
- FR-1a: FastAPI's generated OpenAPI document is the only API contract. `@hey-api/openapi-ts` generates the TypeScript client from that document. Screens call the generated SDK and do not hand-write request types or `fetch` calls. The document has no `/internal` paths.
- FR-1b: The browser reaches the API only through the web origin at `/api`.
- FR-2: The domain layer must provide a long-lived query service that loads an index once and returns passages for retrieval and passages plus the generated answer for RAG.
- FR-3: Eval must accept multiple relevant `doc_ids`, explicit `negative_doc_ids`, questions without a reference answer, and in-memory records. On feedback rows, retrieving a negative document lowers precision and nDCG, unjudged documents are skipped, and a row with negatives but no relevant documents is not treated as unanswerable. Benchmark rows that omit `negative_doc_ids` keep today's scoring. Reference-based metrics are skipped for rows without an answer.
- FR-4: The system must support exactly one shared corpus and one serving index for all signed-in users.
- FR-5: The system must authenticate users with email and password, optionally limit registration to `ALLOWED_EMAIL_DOMAINS`, create admins only with the server-side `create-admin` command or by admin promotion, and restrict all optimization and admin routes to admins.
- FR-6: The system must accept `.txt`, `.md`, and `.pdf` uploads up to 20 MB, extract text with UTF-8 decoding or `pypdf`, and assign `ws_<uuid>` document ids.
- FR-7: The system must index and delete one document at a time through a serialized writer queue without reprocessing unrelated documents, and must resume interrupted document jobs on startup. Full blue-green rebuilds occur only for initial creation, index-time configuration changes, or explicit repair, and switch into service only on success.
- FR-8: Workspace indexes must use `dense`, `hybrid`, or `bm25` retrieval. `graph` and `mixed` are rejected for the workspace. The API owns the index and data location fields of every workspace config.
- FR-9: The system must snapshot every returned hit (rank, `doc_id`, `passage_key`, text, score) and every answer per interaction, and trace each Search and Ask request in MLflow experiment `deepfix_product` without failing the request when tracing fails.
- FR-10: The system must store votes as an append-only event log keyed by user and target, confirm a vote once it is committed locally, and mirror it to MLflow with `mlflow.log_feedback` through a retrying outbox that gives up only on interactions without a trace.
- FR-11: The system must derive eval labels only from passage votes. A document is relevant for a question when at least one of its passages has a positive net vote, and negative when none does and at least one has a negative net vote. Ties, unvoted documents, deleted documents, and unvoted champion output are not labels. A question is exported when it has at least one relevant or negative document.
- FR-12: The system must split feedback questions deterministically by query hash into `dev` (80%) and `holdout` (20%), expose only `dev` to Hermes, keep `holdout` in memory only, and log each snapshot to MLflow experiment `deepfix_product_feedback` without the holdout rows.
- FR-13: The system must report answer votes as an approval rate per champion config, not as eval labels.
- FR-14: The workspace directory contains the corpus, indexes, `dev` feedback, champion config, and run directories. The product database lives outside it. The agent runs in a child process with the workspace as its working directory and an allowlisted environment.
- FR-15: Every optimization run, cron or manual, starts through one in-process function. It skips the run when `dev`, new `dev`, or `holdout` questions are below their configured minimums, and the admin route returns 409 when a run is active. A run that starts launches a new Hermes child process.
- FR-16: The API must promote a candidate only when it validates, uses an allowed backend, passes the `dev` quality gate on the API's own re-score (primary delta at least the configured minimum, no negative secondary delta), and does not regress `holdout` (primary delta at least 0 and holdout wins at least losses). The agent does not apply this gate. Query-time changes reload against the current index; index-time changes require a successful blue-green rebuild before the champion and index switch together.
- FR-17: The API process must schedule one job, `deepfix-optimize-workspace`, default `0 2 * * *` in `Europe/Berlin`, and let admins trigger it, change its cron expression, and enable or disable it. The schedule is not a Hermes gateway cron job.
- FR-18: Runs that exceed the configured timeout must be killed and marked `failed`. Runs left `running` by a restart must be marked `failed`.
- FR-19: Secrets (LLM keys, MLflow credentials, session secret) must stay on the server. The browser never receives them, and it never talks to Hermes directly.

## Non-Goals

- Multiple workspaces, per-user corpora, or collection pickers. There is one shared corpus.
- Microsoft Office uploads, images, audio, HTML archives, and OCR for scanned PDFs.
- Bulk import orchestration for very large corpora. Normal uploads and deletes are incremental per document; full rebuilds remain available for configuration migration and repair.
- `graph` and `mixed` retrieval backends for the workspace index.
- Multi-turn chat memory, suggested questions, and user-written corrected answers.
- Using answer votes or champion answers as reference answers for answer correctness.
- Graded relevance beyond binary labels, and passage-level eval labels. A document is relevant, negative, or unlabeled for a question.
- A custom optimizer inside FastAPI. The Hermes agent forms the hypothesis and runs `deepfix`. API code applies the quality gate and the holdout check.
- Driving optimization through the Hermes gateway HTTP API (ports 8642 and 9119) or through Hermes gateway cron.
- Rolling back to a previous champion from the UI.
- Launching Optuna HPO (`deepfix hpo`) from the UI.
- Editing YAML configs by hand in the UI.
- A hand-written OpenAPI file, or TypeScript API types maintained separately from FastAPI's schema.
- Public self-serve SaaS, billing, SSO, email verification, password-reset email, and audit-log export.
- Replacing the benchmark `dev`/`test` isolation used by research evals. Product feedback lives in separate MLflow experiments.
- Training or fine-tuning embedding or generation model weights.

## Design Considerations

The app is a small internal product with three member destinations and one admin destination. The current `web/` app is a Lovable-generated shell with mock screens. They map to the product like this:

| Screen | Who | Purpose | Replaces |
| :--- | :--- | :--- | :--- |
| Sign in / Create account | Everyone | Email and password. Errors appear under the field they belong to. | (new) |
| Library | Members, admins | Upload, see shared documents and index status, delete own files (admins: any file). | Data upload |
| Search | Members, admins | One query, ranked passages, thumbs on each passage. | Search (mock invoices) |
| Ask | Members, admins | One question, the answer, the passages it used, thumbs on the answer and each passage. | Chat |
| Optimization | Admins | Schedule, Enabled toggle, next run, Run now, recent runs with deltas, champion diff. | Admin (mock metrics) |

The mock "Data visualization" gallery is removed. The System status page stays as a diagnostic page.

- Search and Ask are separate screens. Search never generates an answer in the background.
- Thumbs up and thumbs down are always visible on each result, not hidden in a menu, and each has an accessible label ("Mark passage as relevant", "Mark passage as not relevant", "Mark answer as good", "Mark answer as bad").
- The filled thumb shows the current vote. Pressing it again clears the vote. Pressing the other thumb switches it.
- The Admin link appears only for admins.
- Empty, updating, rebuilding, agent-unavailable, skipped, and rejected states each use one plain sentence saying what happened and what to do next.
- Labels stay short: "Library", "Search", "Ask", "Optimization", "Run now". No marketing landing page.

## Technical Considerations

- **Layers.** Domain code (US-001 service, US-002 eval changes, feedback export, run lifecycle rules, the Hermes runner launcher) lives in `deepfix_rag` modules and is tested without HTTP. `src/deepfix_rag/api/` only parses, calls, and serializes, mirroring the CLI rule in `AGENTS.md`.
- **Frontend.** The TanStack Start app lives in `web/`. It talks to the API only through the client `@hey-api/openapi-ts` generates from `web/openapi.json`, which is FastAPI's own schema (`app.openapi()`, also served at `GET /openapi.json`). `web/src/api.ts` sets the base URL and `credentials: 'include'`. Regenerating the client is part of CI, so a Pydantic model change that is not exported fails the build.
- **Same-origin API.** The browser calls `/api/*` on the web origin, and the web server proxies to FastAPI (Vite proxy in development, the TanStack Start server in production). That keeps the `SameSite=Lax` session cookie working wherever the app is deployed and removes the need for CORS in production. Server-side rendering calls `API_INTERNAL_URL` and forwards the request's `Cookie` header from `web/src/api.ts`.
- **Web conventions.** `web/AGENTS.md` rules still apply. `src/services/*` become thin wrappers over the generated SDK, so features keep calling services. Zod schemas in `src/schemas/` are kept for form validation only; API types come from `src/client`. `web/` is synced with Lovable, so published history on the connected branch is never rewritten.
- **Process model.** The API runs as exactly one uvicorn worker. SQLite, the index-writer queue, the scheduler, the outbox worker, and the in-memory query service all assume a single process. Interrupted work is recovered on startup (US-008, US-014).
- **Product database.** SQLite in the API-private directory holds users, sessions, documents, interactions, hit snapshots, vote events, the MLflow outbox, index builds, the schedule, and optimization runs. It is the source of truth for product state. MLflow holds traces, human feedback assessments, and `dev` dataset snapshots.
- **MLflow.** The Compose server runs MLflow 3.15, which supports tracing and `mlflow.log_feedback`. `pyproject.toml` must require `mlflow>=3`. Each interaction's trace id is stored so feedback attaches to the exact request it judges. Experiments: `deepfix_product` (traces and feedback), `deepfix_product_feedback` (dataset snapshots). Benchmark experiments are untouched.
- **Label policy.** Passage votes are the only labels. Votes are netted per passage first, so different passages of one document never cancel each other, and the document is relevant if any of its passages is up. Long documents are therefore labeled generously, which is acceptable for a pilot; passage-level labels are an open question. Labels exist only for documents the champion showed, so recall measures how many voted-relevant documents a candidate still finds, and judged-only precision and nDCG keep unvoted documents from counting against it.
- **Incremental indexing.** Upload and delete are per-document mutations against the current LanceDB index through one writer queue. The implementation must add domain methods for upsert-by-`doc_id` and delete-by-`doc_id`; the current `_build_index()` whole-corpus path is retained for initial creation, configuration migrations, and repair. BM25 is reconstructed from the vector-store nodes after a mutation, because it is built in memory. The pipeline currently always uses the LanceDB table name `documents` and ignores `retrieval.lancedb_table_name`; the workspace relies on that and does not change it.
- **Promotion and index-time keys.** Index-time keys are `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, and `retrieval.bit_width`. Everything else (for example `top_k`, fusion weights, BM25 parameters, reranking, and generation settings) is query-time and reloads the query service without rebuilding. Index-time changes build a complete candidate index beside the serving index, and the API switches the config and index together only after that build passes.
- **Promotion gate statistics.** With few questions, one question flipping moves a metric by several points, so the defaults ask for 50 `dev` and 15 `holdout` questions before a run starts, and the holdout check requires wins at least equal to losses in addition to a non-negative mean delta. All thresholds are settings, so the pilot can tune them without code changes.
- **Holdout isolation.** `holdout` exists only in memory inside the API process, recomputed from the database when the gate needs it. It is never written to the workspace, the API-private directory, or MLflow. The agent runs in a child process with an allowlisted environment, and in Compose as a separate user that cannot read the API-private directory. Because the API re-scores `dev` itself, a misreported agent number cannot get a candidate promoted.
- **Hermes.** Each run starts a new child process under a dedicated Hermes virtual environment (`HERMES_PYTHON`) built from a pinned checkout of `NousResearch/hermes-agent`, as described in the [Python library guide](https://hermes-agent.nousresearch.com/docs/guides/python-library#basic-usage). The child constructs one `AIAgent` (`quiet_mode=True`, `skip_memory=True`, `max_iterations=15`) and exits when `run_conversation` returns. A child process can be killed on timeout, cannot crash the API, and keeps Hermes dependencies out of the `deepfix_rag` environment. The agent shells out to the `deepfix` binary from the API's own virtual environment, so no separate `uv tool install` is needed and the CLI always matches the API revision. The nightly schedule is an in-process scheduler, not the Hermes gateway.
- **Run ownership.** Run history, skip decisions, timeouts, and promotions live in the API's run table.
- **Performance targets (pilot VM, corpus up to 2,000 documents).** `POST /retrieval` p95 ≤ 2 s with `top_k ≤ 20` and reranking off. `POST /ask` p95 ≤ 15 s. `POST /feedback` p95 ≤ 200 ms, since it only writes locally.
- **Tests.** API tests use the `api` marker. Indexing and query tests use `sample_corpus_path`, `sample_qa_path`, and `populated_sample_index_dir`. Eval tests use `--retrieval-only`. Temporary SQLite files and a temporary local MLflow tracking directory are allowed. No mocks of domain logic, per `AGENTS.md`.

## Success Metrics

- A new member can register, upload a text or PDF file, and get a Search hit from that file within 2 minutes of the upload on a corpus under 500 documents.
- 100% of votes that return HTTP 200 on interactions with a trace appear as MLflow feedback on that trace within 5 minutes while MLflow is reachable.
- At least 20% of Ask interactions receive a vote on the answer or a passage during the pilot.
- Before the end of the pilot, at least one scheduled or manual run promotes a config that passes the `dev` gate and does not regress `holdout`.
- Answer approval rate for the champion after that promotion is at least as high as before it, measured over at least 30 answer votes on each side.
- Members get 403 from every admin route, and never see the Admin link.
- Search and Ask meet the performance targets in Technical Considerations.

## Open Questions

- Should a later version label passages instead of documents, for example by matching voted passage text against a candidate's retrieved chunks, so long documents are not labeled relevant as a whole?
- Should a later version replace binary labels with graded relevance, for example weighting a document by how many votes it received?
- Are the default gate thresholds (50 `dev`, 15 `holdout`, +0.025) reachable within the pilot's vote volume, or should the pilot start lower and raise them?

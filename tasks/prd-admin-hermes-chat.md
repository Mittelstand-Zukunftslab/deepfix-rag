# PRD: Admin Hermes Chat

## Introduction

The Admin section of the DeepFix web app today shows the nightly optimization schedule, the champion diff, and a table of recent runs. An admin can press **Run now** and **Cancel run**, and nothing else. Everything the agent learned during a round — why it rejected a candidate, what an HPO sweep sampled, which metric moved — is only readable by opening the run's MLflow trace or the API-private step log. Turning that into a decision takes a CLI session and an operator.

`tasks/prd-hermes-optimizer.md` already reserves the surface for this: a second Hermes profile named `chat`, on port 8643, with no terminal and no file-write toolset, proxied by the API and never exposed (`FR-15` and “Design Considerations → Admin chat profile” in that document). That profile is written at boot and left stopped (`docker/hermes/sandbox_boot.py` writes `gateway_state.json` with `gateway_state: stopped`). This feature builds the chat on top of it.

This document is the plan. It does not implement the feature.

### The approved architecture

Fadel approved this shape on 2026-11-13. It is fixed for this PRD; the stories assume it and do not re-open it.

```
Browser (assistant-ui in the Admin section)
  |  POST /api/admin/chat/completions   (same origin, Better Auth cookie)
  v
web server (TanStack Start)  -- src/server.ts adds the Better Auth bearer, streams the body
  |
  v
deepfix-api  POST /admin/chat/completions   (admin auth, rate limit, audit)
  |  SSE relay, OpenAI chat.completion.chunk frames
  v
hermes container, chat profile, http://hermes:8643  (API_SERVER_HOST=0.0.0.0, API_SERVER_PORT=8643)
  |  Authorization: Bearer <HERMES_CHAT_API_SERVER_KEY>
  v
OpenRouter via HERMES_CHAT_MODEL, falling back to HERMES_MODEL
```

Everything on the left of the Hermes arrow is DeepFix. The browser never holds an OpenRouter key, a Hermes key, or a model id. Hermes is never reachable from outside the Compose network.

The port and the auth are confirmed from the tree, not assumed: `docker/hermes/sandbox_boot.py` sets `CHAT_API_PORT = 8643` and `CHAT_API_KEY_ENV = "HERMES_CHAT_API_SERVER_KEY"` and writes both `API_SERVER_PORT=8643` and `API_SERVER_KEY=<chat key>` into `profiles/chat/.env`; `compose.hermes.yml` passes `HERMES_CHAT_API_SERVER_KEY` from the environment and publishes no ports. The optimization profile keeps 8642 with the separate `API_SERVER_KEY`.

Optimizations started from chat do **not** ride the chat stream. They go to the existing job API: `start_optimization_run`, `recent_runs`, `cancel_optimization_run`, `hermes_run_id`, the step log, and the stall/timeout rules from `tasks/prd-hermes-optimizer.md` US-004 through US-008. The chat shows that job's progress and lets the admin reconnect to it after a reload.

### Clarifying questions, answered from the brief

The create-prd skill asks three to five questions before a PRD is written. The brief answers them; no question blocks the draft.

1. What is the primary goal?
   A. Let an admin read, question, and steer the optimization from a chat in the Admin section, without gaining any new power to promote.
   B. A general-purpose assistant on every screen.
   C. A replacement for the Optimization screen.
   Answer: **A.** The Optimization screen keeps its schedule, champion, run table, Run now, and Cancel run. Chat is added beside it.

2. Where does the chat live?
   A. A new admin-only route in the existing web app, under the Admin navigation group, at `/admin/chat`.
   B. A separate app or a floating widget on every page.
   C. Inside the existing Ask screen.
   Answer: **A.** The Ask screen is member-facing, per-user, and answers corpus questions; mixing the two leaks an admin surface into a member page.

3. How does chat reach Hermes?
   A. The browser calls a new admin-only deepfix-api route, which proxies the Hermes OpenAI-compatible endpoint and relays SSE.
   B. The browser calls Hermes directly with a key injected at page load.
   C. The browser opens a WebSocket to the API.
   Answer: **A.** Approved by Fadel. B is excluded by the credential rule. C adds a second protocol for no benefit.

4. What may the chat change?
   A. Nothing directly. It explains, proposes text, and can hand the admin a typed action card that only the admin can confirm; the confirm calls the existing domain functions.
   B. It may call tools that start, cancel, and promote.
   Answer: **A.** Promotion stays inside `apply_candidate` (`src/deepfix_rag/promotion.py`), which no chat path may call.

5. How long may one chat turn run?
   A. Bounded. A turn is a conversation step, not a training job; long work is a job, and the chat watches it.
   B. Unbounded, up to the agent's own limits.
   Answer: **A.** The VPS has 7.6 GB and Hermes has a 4 GB limit. See NFR-2.

## Amendments required in `tasks/prd-hermes-optimizer.md`

Do **not** edit that file as part of writing this document. After this PRD is accepted, a follow-up edit should align it with what CHAT-001 and CHAT-011 change:

1. **“Design Considerations → Admin chat profile”** currently ends with “It is not started as the optimization agent.” That stays true, but the profile is now started as its own chat gateway. Add one sentence pointing at `tasks/prd-admin-hermes-chat.md` and note that `HERMES_CHAT_ENABLED` controls it.
2. **US-001 acceptance criterion** (“The `chat` profile's port 8643 is also unpublished. Its contract is in Design Considerations. It is not started as the optimization agent.”) is still satisfied: unpublished and not the optimization agent. No change needed, but a reader should not have to reconcile it with a running chat gateway.
3. **US-007 follow-up** states “The chat profile's config, `.env`, and home do not get the plugin or `hermes_otel.yaml`.” CHAT-011 reverses that on purpose, because a chat turn must be traceable. The sentence becomes: the chat profile gets the same plugins with its own `profiles/chat/hermes_otel.yaml` and its own experiment (`MLFLOW_HERMES_CHAT_EXPERIMENT`), still with `content_capture: "off"`.
4. **US-006 toolset note** says the chat profile's `api_server` list stays `skills`, `web`, `vision`, `todo`, `memory`. That is unchanged, and CHAT-001 adds a test that `terminal`, `file`, and `code_execution` stay absent.
5. **FR-15** (“A future admin chat uses the separate `chat` Hermes profile … and the API proxies that profile.”) is satisfied by this PRD and needs no edit. It is cited here so a reviewer can confirm the boundary was planned, not invented.

Do not reopen or uncheck US-001 through US-010 of that document.

## Glossary

- **Admin chat** — the chat area in the Admin section that talks to the `chat` Hermes profile. Not the member Ask screen.
- **Chat profile** — the Hermes profile named `chat`. Own home, own `config.yaml`, own `.env`, own API server on port 8643, own bearer key. Optimization uses the default profile on 8642.
- **Turn** — one user message plus the assistant reply streamed for it. Identified by `turn_id`. A turn is not a job.
- **Thread** — an ordered list of turns, shown in the chat's history list. Identified by `thread_id`.
- **Job / run** — one optimization round owned by `optimization_runs`, identified by the DeepFix `run_id`. A job outlives the chat turn that started it.
- **Action card** — a UI element the admin must confirm. The only way chat can cause a write.
- **Step line** — one structured log line for a job event, already written by `_write_step` / `_step_line` in `src/deepfix_rag/optimization.py`.
- **Gate** — `OPTIMIZATION_MIN_PRIMARY_DELTA` (0.025), `OPTIMIZATION_SECONDARY_TOLERANCE` (-0.01), and the holdout check inside `apply_candidate`.
- **Holdout** — the 20% of labeled questions that never leave the API process (`feedback.is_holdout`).

## Goals

- **G1** Give an admin a conversational place in the Admin section to ask what happened, what to try next, and to start, watch, and cancel an optimization round — usable with a keyboard and a screen reader.
- **G2** Keep every credential server-side. The browser speaks only to deepfix-api through the existing same-origin `/api` proxy.
- **G3** Leave the gate, the holdout split, and promotion exactly where they are. Chat never scores holdout, never writes `configs/champion.yaml`, and never promotes.
- **G4** Make long work visible and recoverable: streamed replies, tool and job progress, cancel, retry, and reconnect after a page reload or a dropped connection.
- **G5** Stay inside the server envelope: 4 GB and 2 CPUs for Hermes, one optimization at a time, and a hard bound on chat concurrency and turn length.
- **G6** Make every chat-initiated action traceable: an MLflow trace with the thread and turn id, and an append-only audit log in the API's SQLite database.

## Scope

### In scope

- Starting the `chat` Hermes profile in the existing container, with the isolation it was designed for.
- A new domain module and SQLite tables for threads, turns, messages, action cards, and audit events.
- One new admin-only streaming route and a small set of admin-only JSON routes on deepfix-api.
- A resumable per-turn stream, so a reload does not lose the reply in progress.
- Run progress for a thread, read from `optimization_runs` and the existing step log.
- An admin-only `/admin/chat` route in the web app, built with `@assistant-ui/react`.
- Tracing for the chat profile in MLflow, tagged with the thread and turn id.

### Out of scope (this release)

- Chat for members, or on any route other than `/admin/chat`.
- Attachments, images, voice, or dictation.
- Model selection in the UI.
- Scheduled or background chat jobs. A turn is request-scoped; scheduling stays in `optimization_schedule`.
- Deleting, editing, or replaying an optimization run's config from chat.
- A chat-driven HPO sweep distinct from the one the optimization skill already runs.

### Won't (explicitly, forever, not just this release)

- Chat promoting a config, writing `configs/champion.yaml`, or switching `indexes/current`.
- Chat receiving holdout questions, holdout query ids, or holdout document ids.
- A browser tab holding `OPENROUTER_API_KEY`, `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, or an S3 secret.
- A published port or a public route for Hermes, or enabling its dashboard.
- A second, chat-specific model id hardcoded in Python or TypeScript.

## Personas / actors

- **Admin (Fadel or an operator)** — the only role that can open the page. Wants to ask “why did last night's run reject?”, “what did the sweep sample?”, “start a run”, and “cancel it” without a terminal.
- **Hermes chat profile** — the conversational agent. Explains, summarises, and proposes. It has no shell, no file write, no S3 key, and no admin password.
- **deepfix-api** — the only component that holds credentials, counts turns, writes the audit log, and executes a confirmed action.
- **Operator** — deploys and verifies on Dokploy. Not a chat user.
- **Member** — must not reach the page, the routes, or the stream. Sees the existing denied page.

## User journeys

### J1 — Ask what happened, on a fresh page load (FR-004, FR-005, FR-006, FR-007, FR-010, FR-021)

1. Admin opens `/admin/chat`. The page loads the thread list, newest first, and opens the most recent thread, or an empty state.
2. The header shows the current run status: idle, or “Running since 02:00”, or “Last run rejected: …”.
3. Admin types “why did the last run reject?”. The composer sends `POST /api/admin/chat/completions` with the thread's messages.
4. deepfix-api checks the admin role, counts the admin's recent turns, writes `turn_started` to the audit log, prepends the server-side system prompt and a status digest, and forwards to `http://hermes:8643/v1/chat/completions` with `stream: true`.
5. Assistant text arrives as `chat.completion.chunk` frames. The UI renders markdown as it arrives, scrolls with the viewport, and shows a stop button in place of send.
6. When the turn ends, the assistant message is stored, the thread's `updated_at` moves, and any action card the reply proposed appears below the message.

### J2 — Start a run, watch it, come back later (FR-013, FR-014, FR-016, FR-018)

1. The reply proposes “Start a nightly run now”. An action card renders with the run's preconditions (dev count, new dev count, holdout count) read from `GET /admin/optimization`.
2. Admin presses **Confirm**. The browser calls `POST /admin/chat/actions/{action_id}/confirm`. deepfix-api checks the action belongs to this admin and this thread, is unconfirmed and unexpired, writes `action_confirmed`, and calls the same domain function the Optimization screen's Run now uses.
3. The response is the existing `OptimizationRunResponse`. The thread now shows a run panel: status, start time, and the last step lines.
4. Admin closes the tab. Ten minutes later they reopen the thread: the run panel re-reads the run, still `running`, and keeps polling every 5 seconds while the row is `running`.
5. The run finishes. The panel shows the final status, the reason, the dev delta, the aggregate holdout delta, and the MLflow link, exactly as the Optimization table does. No holdout rows.

### J3 — Stop a long reply, or retry it (FR-008, FR-009)

1. A turn runs past 30 seconds. Admin presses **Stop**.
2. The browser aborts its fetch. deepfix-api sees the disconnect, closes the upstream Hermes request, marks the turn `cancelled`, and writes `turn_cancelled`. The partial assistant text stays visible and is stored.
3. Admin presses the retry control on the last assistant message. The browser sends the same user message again; deepfix-api records a new turn with `retry_of` set to the cancelled one and streams the new reply.

## User stories

Stories are ordered. Each is one implementation session on `feat/hermes-optimizer`, independently testable, and committed on its own. This PRD does not check them off. `CHAT-012` is an operator session after deployment; a cloud agent cannot run it.

### CHAT-001: Start the chat profile and prove it answers

**Description:** As an operator, I want the `chat` Hermes profile running on port 8643 inside the container, so the API can talk to it and nothing else can.

**Acceptance Criteria:**

- [ ] `docker/hermes/sandbox_boot.py` starts the `chat` profile instead of writing `gateway_state: stopped`, controlled by `HERMES_CHAT_ENABLED` (default `true`). The exact start mechanism on the pinned image (`nousresearch/hermes-agent` v0.22.7) is recorded in the story's verification note. Today the boot writes `profiles/chat/gateway_state.json` as stopped and the Dockerfile comment says the profile is not started; that comment is updated.
- [ ] The chat profile's `.env` contains `API_SERVER_ENABLED=true`, `API_SERVER_HOST=0.0.0.0`, `API_SERVER_PORT=8643`, and `API_SERVER_KEY` taken from `HERMES_CHAT_API_SERVER_KEY` only when it passes `validate_api_key` (at least 16 characters, no placeholder). It contains no `OPENROUTER_API_KEY`, no `S3_*`, no `LLM_API_KEY`, and no optimization `API_SERVER_KEY`. `test_chat_profile_env_is_replaced` in `tests/test_hermes_sandbox.py` still passes and gains an assertion that the chat key is present in the chat `.env` and absent from the optimization `.env`.
- [ ] The chat profile's model comes from `HERMES_CHAT_MODEL`, falling back to `HERMES_MODEL` when unset or blank. Both are written into the chat `config.yaml` by `chat_profile_config`. No model literal appears in `sandbox_boot.py`, the Compose file, or the image.
- [ ] `chat_profile_config` keeps the toolset list free of `terminal`, `file`, and `code_execution`, and the shipped `CHAT_API_TOOLSETS` stays `("skills", "web", "vision", "todo", "memory")`. A test asserts those three names are absent.
- [ ] `compose.hermes.yml` gains `HERMES_CHAT_API_SERVER_KEY: ${HERMES_CHAT_API_SERVER_KEY:-}`, `HERMES_CHAT_ENABLED`, and `HERMES_CHAT_MODEL`. It still publishes no ports, keeps `mem_limit: 4g` and `cpus: "2"`, and its healthcheck still hits `http://127.0.0.1:8642/health` on the optimization profile. The chat health check is a separate Compose healthcheck or is done from the API (see the next criterion), not by replacing the optimization one.
- [ ] `tests/test_hermes_sandbox.py` (or a new `tests/test_hermes_chat_profile.py`) asserts: both profiles are configured, the two bearer keys differ, the chat profile has no S3 or OpenRouter key, and the optimization profile's eval env file is not readable from the chat profile's config.
- [ ] From the API container, `GET http://hermes:8643/health` returns `{"status": "ok"}`, and `POST http://hermes:8643/v1/chat/completions` with `{"model": "hermes-agent", "messages": [{"role": "user", "content": "say ok"}]}` and the chat bearer returns a `chat.completion`. Recorded in the verification note; this VM has no container runtime, so the story records what it could and could not execute, like US-001 did.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-002: Chat store and audit tables

**Description:** As a developer, I want one module that owns threads, turns, messages, action cards, and audit rows, so routes stay thin.

**Acceptance Criteria:**

- [ ] A new domain module `src/deepfix_rag/admin_chat.py` owns the schema and every read and write, using the existing `deepfix_rag.storage.connect` context manager and the same SQLite file as `optimization_runs` (`settings.product_db_path`). It exposes `init_admin_chat(db_path)` and is called from the API lifespan next to `init_optimization` in `src/deepfix_rag/api/app.py`.
- [ ] Tables, created with `CREATE TABLE IF NOT EXISTS`, are additive only (no change to `optimization_runs`):

| Table | Columns |
| :--- | :--- |
| `admin_chat_threads` | `thread_id` PK, `title`, `created_by`, `created_by_email`, `created_at`, `updated_at` |
| `admin_chat_turns` | `turn_id` PK, `thread_id`, `admin_id`, `admin_email`, `status` (`running`/`completed`/`failed`/`cancelled`), `hermes_session_id`, `retry_of`, `error`, `request_chars`, `first_token_ms`, `started_at`, `ended_at` |
| `admin_chat_messages` | `message_id` PK, `thread_id`, `turn_id`, `role` (`user`/`assistant`), `seq` INTEGER, `content`, `created_at` |
| `admin_chat_actions` | `action_id` PK, `thread_id`, `turn_id`, `kind`, `payload`, `status` (`proposed`/`confirmed`/`rejected`/`expired`/`failed`), `created_at`, `expires_at`, `decided_at`, `decided_by`, `result` |
| `admin_chat_thread_runs` | `thread_id`, `run_id`, `linked_at`; primary key `(thread_id, run_id)` |
| `admin_chat_audit` | `event_id` INTEGER PK AUTOINCREMENT, `at`, `admin_id`, `admin_email`, `thread_id`, `turn_id`, `kind`, `detail` |

`admin_chat_thread_runs.run_id` carries no foreign key to `optimization_runs`: a linked run is a pointer for display, and a pruned run row must not cascade-delete a thread.

- [ ] `kind` on `admin_chat_audit` accepts exactly these values, enforced by a `CHECK`: `turn_started`, `turn_completed`, `turn_cancelled`, `turn_failed`, `rate_limited`, `action_proposed`, `action_confirmed`, `action_rejected`, `action_expired`.
- [ ] Indexes exist for the reads the routes make: `admin_chat_threads (created_by, updated_at DESC)`, `admin_chat_messages (thread_id, seq)`, `admin_chat_turns (thread_id, started_at)`, `admin_chat_turns (admin_id, started_at)` for the rate-limit count, and `admin_chat_actions (thread_id, status)`. Created with `CREATE INDEX IF NOT EXISTS` so an existing database picks them up.
- [ ] `create_thread(db_path, admin, title=None) -> Thread`, `list_threads(db_path, admin_id, limit=50)`, `get_thread(db_path, thread_id, admin_id)`, `delete_thread(db_path, thread_id, admin_id)`, `append_message(...)`, `update_assistant_text(...)`, `start_turn(...)`, `finish_turn(...)`, `thread_runs(...)`, `record_audit(...)`, `count_recent_turns(...)` exist as typed functions. A thread and its turns are visible only to the admin id that created them; a second admin gets `KeyError`, which the route maps to 404.
- [ ] Deleting a thread hard-deletes its turns, messages, and unconfirmed actions. Audit rows are never deleted.
- [ ] Secret redaction covers the chat key. The redaction helper the module exposes is built on `optimization._secrets` / `optimization._redact`, and `HERMES_CHAT_API_SERVER_KEY` is added to the `_SECRET_ENVS` tuple in `src/deepfix_rag/optimization.py` (today it lists `API_SERVER_KEY`, `OPENROUTER_API_KEY`, `S3_SECRET_ACCESS_KEY`, `HERMES_S3_SECRET_ACCESS_KEY`, and `LLM_API_KEY` only). A test sets a chat key, passes it through the helper, and asserts `[redacted]` and no substring of the key.
- [ ] A thread title defaults to the first 60 characters of its first user message (whitespace collapsed), with no model call.
- [ ] Tests use `tmp_path` and the real SQLite file. No mocks. They cover: create/list/get/delete isolation between two admin ids, audit `kind` rejection of an unknown value, `expires_at` handling, and ordering by `updated_at` descending.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass, and `uv run --env-file .env pytest -m "not network and not e2e"` includes the new tests.

### CHAT-003: The streaming proxy route

**Description:** As an admin, I want my message to reach Hermes and its reply to stream back, without any key in my browser.

**Acceptance Criteria:**

- [ ] `POST /admin/chat/completions` (`operation_id` `admin_chat_completion`) in a new `src/deepfix_rag/api/routes/admin_chat.py`, registered in `app.py`. It calls `require_admin` from `src/deepfix_rag/api/deps.py`: 401 without a bearer, 403 for a member. The route handler does no domain work itself; it calls `admin_chat`.
- [ ] The request body is OpenAI-shaped and validated by a Pydantic model, not passed through: `thread_id: str`, `messages: list[{role: "user" | "assistant", content: str}]`, `retry_of: str | None`. The server builds the upstream `messages` from the thread's stored messages plus the last element of the request, which must be a `user` message. If the request's last element is not a `user` message, or its earlier elements disagree with what the thread already stored, the route returns 400 “The conversation is out of date. Reload the thread.” — the client cannot rewrite history or the prompt.
- [ ] Limits, all from settings, all with a default and a documented env name: total request characters `ADMIN_CHAT_MAX_PROMPT_CHARS` (default 48000), messages per request `ADMIN_CHAT_MAX_MESSAGES` (default 60), and wall-clock per turn `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` (default 300). Exceeding a size limit is 400 with a readable sentence, not a stack trace.
- [ ] The upstream call is `POST http://hermes:8643/v1/chat/completions` with `Authorization: Bearer <HERMES_CHAT_API_SERVER_KEY>`, `Content-Type: application/json`, `stream: true`, `model` from `HERMES_CHAT_MODEL` (falling back to `HERMES_MODEL`), `messages` = server system message + the thread's stored history + the new user message, and `X-Hermes-Session-Id` set to the thread's `hermes_session_id` once known. The base URL comes from `HERMES_CHAT_BASE_URL` (default `http://hermes:8643`).
- [ ] `require_model`-style behaviour: if `HERMES_CHAT_API_SERVER_KEY` is empty or `HERMES_CHAT_MODEL`/`HERMES_MODEL` is empty, the route returns 503 with “Admin chat is not configured on this server.” and does not call Hermes. No model id is hardcoded.
- [ ] The reply is relayed as SSE, byte-for-byte in order, with `media_type="text/event-stream"`, `Cache-Control: no-cache`, `Connection: keep-alive`, and `X-Accel-Buffering: no`. Hermes `: keepalive` comments are forwarded. The route does not buffer the whole reply: the test server delays its second chunk, and the test asserts the first frame reached the client before that delay ended.
- [ ] `Content-Type: application/json` (non-streaming) is refused with 400. The chat UI always streams.
- [ ] On completion the route stores the assistant text, sets `first_token_ms` and `ended_at`, marks the turn `completed`, appends `turn_completed`, and emits one final SSE frame with a JSON object `{"type": "deepfix.turn", "turn_id", "status", "actions": [...]}` before closing.
- [ ] `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` is enforced as a wall-clock deadline on the relay. On expiry the route emits `{"type": "deepfix.error", "message": "This reply took too long and was stopped."}`, marks the turn `failed` with that reason, closes the upstream request, and writes `turn_failed`. The partial text stays stored, exactly as for a cancel.
- [ ] On an upstream error before any token, the route returns 502 with a readable sentence. After the first token it emits `{"type": "deepfix.error", "message": ...}` and closes. Neither path includes the upstream body verbatim; both pass through the secret redaction helper (`optimization._redact` today) so a key echoed by a misconfigured server cannot reach the browser. A test asserts `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, and `OPENROUTER_API_KEY` do not appear in any error frame or audit `detail`.
- [ ] The turn is recorded in the audit log as `turn_started` before the upstream call and `turn_completed` / `turn_failed` after it, with the admin id and email, the thread id, and the turn id.
- [ ] Tests run against a real local HTTP server that stands in for the chat profile (no `unittest.mock`), covering: 401, 403, a streamed reply split across several chunks, a `: keepalive` forward, upstream 500 before the first token, an error after the first token, a configured-empty key giving 503, an oversized prompt giving 400, a `system` role giving 400, a request whose history disagrees with the stored thread giving 400, and the upstream body containing exactly the stored thread history plus the new user message.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass, and the OpenAPI document regenerates (`uv run python -m deepfix_rag.api.export_openapi`) with the new operation id.

### CHAT-004: Server prompt and status digest

**Description:** As an admin, I want the assistant to know the current schedule, champion, and recent runs, so its first answer is about my server rather than about itself.

**Acceptance Criteria:**

- [ ] `admin_chat.system_prompt(digest)` returns a fixed policy text plus a per-turn digest. The policy text says: this is the DeepFix admin chat; the assistant explains and proposes, it does not promote; it must not claim to have promoted anything; it must not ask for or repeat credentials; it names the gate thresholds when it talks about promotion; it may propose an action card with the exact block described in CHAT-007.
- [ ] The digest is built from data the admin screen already returns, using the existing domain functions: `optimization_schedule` view (cron, enabled, next run, agent, availability), `champion_diff(workspace)`, and `recent_runs(db_path, limit=10)` with status, trigger, timestamps, reason, `primary_metric`, `dev_delta`, `holdout_delta`, `holdout_wins`, `holdout_losses`, and `mlflow_run_id`.
- [ ] The digest contains no holdout question, no holdout query id, no document text, no filesystem path under `PRODUCT_DATA_DIR`, no `run_bundle` object key, and no credential. `private_paths` from `OptimizationSettings` are filtered out the same way `_redact` filters the run log.
- [ ] The digest is capped at `ADMIN_CHAT_DIGEST_MAX_CHARS` (default 6000) and degrades by dropping the oldest runs first, never by truncating a line mid-sentence.
- [ ] `system_prompt` takes a plain digest dict and is unit-tested without the pipeline: a fixture digest renders deterministically, an empty database renders the “no runs yet” wording, and a digest containing a holdout id or a private path is refused (raises) rather than sent.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-005: Threads, resume, and cancel

**Description:** As an admin, I want my threads to persist and a reply in progress to survive a reload, so closing the tab does not lose work.

**Acceptance Criteria:**

- [ ] `GET /admin/chat/threads` (`list_admin_chat_threads`) — newest first, admin's own threads only, `limit` default 50. Each item is `thread_id`, `title`, `updated_at`, `last_turn_status`.
- [ ] `GET /admin/chat/threads/{thread_id}` (`get_admin_chat_thread`) — the thread, its messages in `seq` order, its turns with status, and its actions. 404 for an unknown id or another admin's thread.
- [ ] `POST /admin/chat/threads` (`create_admin_chat_thread`) and `DELETE /admin/chat/threads/{thread_id}` (`delete_admin_chat_thread`). Delete is idempotent for the owner and returns 204.
- [ ] `GET /admin/chat/turns/{turn_id}/stream` (`resume_admin_chat_turn`) — SSE. It first replays the assistant text already stored for the turn, then, while the turn is `running` in this API process, forwards new deltas until the terminal frame. If the turn is not running here it sends the stored text and one terminal frame with the stored status.
- [ ] A browser abort on `POST /admin/chat/completions` marks the turn `cancelled`, writes `turn_cancelled`, closes the upstream Hermes request, and keeps the partial assistant text. It does **not** call `cancel_optimization_run`: a chat turn and a job are separate, and cancelling a reply must never stop a run.
- [ ] API startup calls `fail_interrupted_chat_turns(db_path)` next to `recover_running_runs` in `app.py`, marking turns left `running` by a restart as `failed` with “Interrupted: the API restarted while this reply was in progress.”
- [ ] A retry request carries `retry_of` set to the turn being retried; that id must be a turn in the same thread, otherwise 400. The new turn stores it and the thread shows both turns.
- [ ] Tests: reload mid-stream yields the stored partial text and then the live tail; an abort during streaming yields `cancelled` plus the partial text; a simulated restart marks the turn `failed` and a later resume returns that status; a second admin's thread id returns 404; deleting a thread removes its messages and leaves audit rows.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-006: Rate limit and audit read

**Description:** As the operator, I want the chat to be bounded and reviewable, so a stuck browser tab or a prompt loop cannot exhaust the model budget or the 4 GB container.

**Acceptance Criteria:**

- [ ] Rate limiting counts persisted turns, so it works across processes and restarts: per admin `ADMIN_CHAT_MAX_TURNS_PER_WINDOW` (default 30) inside `ADMIN_CHAT_TURN_WINDOW_SECONDS` (default 300), and one `running` turn per admin. A request over the limit is 429 with a readable sentence naming the wait, `Retry-After` in seconds, and one `rate_limited` audit row that is itself at most one row per minute per admin.
- [ ] A global cap `ADMIN_CHAT_MAX_CONCURRENT_TURNS` (default 2) returns 429 “The assistant is busy. Try again in a moment.” and writes no audit row.
- [ ] `GET /admin/chat/audit?limit=50` (`list_admin_chat_audit`) returns the newest audit rows, including `admin_email`, `thread_id`, `turn_id`, `kind`, and `detail`. Admin only. A member receives 403.
- [ ] A `rate_limited` row is written by the limiter itself and never causes a second limiter pass: the count used by the limiter is `count_recent_turns`, which reads `admin_chat_turns`, so audit writes cannot inflate it and a refusal cannot ratchet itself. The “one per minute per admin” rule is a `SELECT` on the newest `rate_limited` row for that admin.
- [ ] Every audit `detail` is redacted through the same helper as the step log and truncated to 500 characters, matching `STEP_PREVIEW_LIMIT`.
- [ ] Tests: the 31st turn in a window is 429; two `running` turns for one admin is 429; the global cap is enforced with two admins; a member gets 403 on the audit route; a `detail` containing a secret is stored redacted.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-007: Action cards

**Description:** As an admin, I want the assistant to be able to hand me a button I press myself, so chat can start or cancel a run without ever holding that power.

**Acceptance Criteria:**

- [ ] A completed assistant reply is scanned for fenced blocks whose info string is `deepfix-action`, containing one JSON object with `kind` and `reason`. The recognised `kind` values are exactly `start_optimization_run` and `cancel_optimization_run`. At most two cards are stored per reply; further blocks are dropped. Any other `kind`, a malformed block, or a dropped extra is counted in the turn's audit `detail`; it is never an error the admin must handle.
- [ ] A recognised block becomes an `admin_chat_actions` row with `status: proposed` and `expires_at` = now + `ADMIN_CHAT_ACTION_TTL_SECONDS` (default 600). `cancel_optimization_run` carries the `run_id` only when that id is a row in `optimization_runs`; otherwise the block is dropped.
- [ ] `POST /admin/chat/actions/{action_id}/confirm` (`confirm_admin_chat_action`) requires admin auth, the owner's thread, `status = proposed`, and an unexpired row. `start_optimization_run` calls the same domain function with the same `options=settings_from(settings)` and `coordinator` that `POST /admin/optimization/run` uses. `cancel_optimization_run` calls `cancel_optimization_run`. The response is the existing `OptimizationRunResponse`. `RunInProgress`, `AgentUnavailable`, `KeyError`, and `RunNotRunning` map to 409 / 503 / 404 / 409 exactly as the existing routes do.
- [ ] `POST /admin/chat/actions/{action_id}/reject` (`reject_admin_chat_action`) marks it `rejected`. A second confirm on the same action is 409. An expired row is marked `expired` on read and returns 409.
- [ ] Confirming writes `action_confirmed` with the resulting `run_id`; rejecting writes `action_rejected`. The confirm route is the only chat path that can change run state.
- [ ] A test enumerates every public function in `admin_chat` and asserts none of them imports or calls `apply_candidate`, writes `configs/champion.yaml`, or reads holdout (`feedback.is_holdout`, `_holdout_records`). A test confirms `start_optimization_run` is reached with the same arguments as the Optimization route.
- [ ] Tests: a reply with one valid block yields one card; an unknown `kind` yields none; a second admin cannot confirm another admin's card (404); an expired card is 409 and audited `action_expired`; a double confirm is 409; confirmation of a start when a run is already running is 409 with the existing message.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-008: Run progress for a thread

**Description:** As an admin, I want the chat to show the optimization job it started, including CLI steps and eval results, so I can follow a multi-hour run without leaving the page.

**Acceptance Criteria:**

- [ ] `POST /admin/chat/threads/{thread_id}/runs/{run_id}` links a run to a thread (called by the UI after a confirmed start, or by the confirm route), and `GET /admin/chat/threads/{thread_id}/runs` (`list_admin_chat_thread_runs`) returns each linked run. Linking an unknown `run_id` is 404; linking the same pair twice is idempotent and returns 200.
- [ ] Each run item is `run_id`, `status`, `trigger`, `reason`, `started_at`, `ended_at`, `primary_metric`, `dev_delta`, `holdout_delta`, `holdout_wins`, `holdout_losses`, `mlflow_run_id`, `mlflow_url`, and `steps`: the last `ADMIN_CHAT_STEP_TAIL` (default 50) lines of `optimization-logs/<run_id>.log` under `PRODUCT_DATA_DIR`, redacted and each truncated to 500 characters.
- [ ] The step tail is read with a bounded read, not by loading the file: the reader seeks to the end, reads backwards at most `ADMIN_CHAT_STEP_READ_BYTES` (default 65536) bytes, and splits lines. A 10 MB log must not be read into memory, and the response must not grow with the log. A test writes a 2 MB log and asserts the read stays under the byte bound.
- [ ] The step tail never includes the `----- output -----` section, holdout question text, or any private path. A test seeds a step log containing a secret and a holdout query and asserts neither appears in the response.
- [ ] `GET` calls `fail_stale_runs` with the same arguments `GET /admin/optimization` uses, so a dead job is not reported as `running` forever.
- [ ] Linked runs are deleted with the thread only in the join table; `optimization_runs` rows are never touched.
- [ ] Tests: two linked runs come back newest first; a run owned by another thread is not returned; the step tail is capped and redacted; a member gets 403.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-009: Web data layer for admin chat

**Description:** As a frontend developer, I want a typed client and an SSE reader, so the UI code never hand-writes request types or parses frames twice.

**Acceptance Criteria:**

- [ ] The generated client is regenerated from `web/openapi.json` with `npm run generate:api` (`@hey-api/openapi-ts` 0.99.0). No hand-written `fetch` for a JSON route; the streaming route uses one small module because the generated client does not stream.
- [ ] `web/src/services/admin-chat-service.ts` exposes `threads()`, `thread(threadId)`, `createThread()`, `deleteThread()`, `streamTurn({ threadId, messages, retryOf, signal, onChunk, onTool, onAction, onDone, onError })`, `resumeTurn(turnId, …)`, `confirmAction(actionId)`, `rejectAction(actionId)`, `linkRun(threadId, runId)`, and `runs(threadId)`. Each JSON call goes through a server function in `web/src/services/admin-chat.functions.ts` guarded by `requireRole("admin", …)` from `src/lib/auth-middleware.ts`, matching `optimization.functions.ts`.
- [ ] `streamTurn` posts to `/api/admin/chat/completions` with `credentials: "include"`, `Accept: text/event-stream`, and an `AbortController`. It parses `data:` frames, tolerates `: keepalive` comments, tolerates a frame split across reads, and calls `onDone` once.
- [ ] Contracts are Zod schemas in `web/src/schemas/admin-chat.ts` (thread, message, turn, action, run summary), and the UI imports types from there.
- [ ] `web/src/test/admin-chat-stream.test.ts` runs the parser against three fixtures: a normal multi-chunk reply, a reply with a `deepfix.turn` frame carrying an action, and a truncated stream followed by an abort. It asserts the accumulated text, the action payload, and that the abort surfaces once.
- [ ] `bun run typecheck` passes and `bun run test src/test/admin-chat-stream.test.ts` is green.
- [ ] No model id, key, or Hermes host appears in `web/src`. `web/src/test/admin-chat-boundary.test.ts` reads every file under `web/src` except its own file and fails on `8643`, `HERMES_CHAT`, `OPENROUTER`, `API_SERVER_KEY`, or `hermes:`. It also asserts the only chat route strings in the app are the same-origin `/api/admin/chat/...` paths. Today that scan finds nothing, so the test guards the future rather than the present.

### CHAT-010: The chat UI in the Admin section

**Description:** As an admin, I want a usable chat page, so I can read a streamed answer, see progress, stop it, retry it, and confirm an action without leaving the Admin section.

**Acceptance Criteria:**

- [ ] A new route `web/src/routes/admin_.chat.tsx` — the `_` suffix stops it nesting under `admin.tsx`, which renders no `<Outlet />` and would otherwise swallow the child, while the URL stays `/admin/chat` — keeps the `admin.tsx` shape: `head`, a loader that returns `null` for a non-admin, and a component that renders `<AdminChatDenied />` for a member. The page lives in `web/src/features/admin-chat/`, per `web/AGENTS.md`. `routeTree.gen.ts` regenerates; it is not edited by hand.
- [ ] Navigation gains `/admin/chat` labelled “Hermes chat” with a `Sparkles` or `Bot` icon, placed directly after “Optimization”. It stays in `ADMIN_PATHS` in `web/src/components/layout/navigation.ts`, so a member's sidebar does not show it, and `AppPath` in `web/src/components/common/nav-link.tsx` gains the literal.
- [ ] Active-state matching does not double-highlight. `AppSidebar` today computes `active.startsWith(item.to)`, so on `/admin/chat` both “Optimization” (`/admin`) and “Hermes chat” would render as the current page. The check becomes an exact match or longest-prefix match, and a test renders the sidebar at `/admin/chat` and at `/admin` and asserts exactly one item has the active class in each case.
- [ ] The chat is built with `@assistant-ui/react` `^0.15.28` (already in `web/package.json`) using `AssistantRuntimeProvider` with `useExternalStoreRuntime`, the same pattern `web/src/features/chat/chat-page.tsx` already uses, and `ThreadPrimitive`, `MessagePrimitive`, `ComposerPrimitive`, `ThreadListPrimitive`, and `ActionBarPrimitive`. `onNew` sends a turn, `onReload` sends a retry, `onCancel` aborts and records the cancel. Each of those three handlers exists; the story's verification note records that the installed 0.15.28 supports them.
- [ ] **Streaming:** assistant text appends as frames arrive, with no reload of the thread. The viewport follows the stream and stops following when the admin scrolls up.
- [ ] **History:** a thread list on the left, newest first, with a **New chat** control and a delete control per thread. Selecting a thread loads its messages. A reload reopens the last thread from the URL (`?thread=<id>`) or the newest thread.
- [ ] **Markdown and code:** assistant text renders with `react-markdown` and `remark-gfm` (already dependencies). Fenced code renders in a bordered `pre > code` block with a copy button and no syntax highlighter. User messages render as plain text, never as HTML.
- [ ] **Tool and progress:** a `hermes.tool.progress` event renders as a collapsed step row (“Searching the web…”, then its result preview). A linked optimization run renders a run panel: status, elapsed, reason, dev delta, aggregate holdout delta, wins/losses, MLflow link, and the step tail, polled every 5 seconds while the run is `running` and once on mount.
- [ ] **Stop and retry:** while a turn runs the composer shows **Stop**; pressing it aborts, keeps the partial text, and shows “Stopped”. Each assistant message has a retry control that resends its user message.
- [ ] **Actions:** an action card shows what it will do in one sentence (`Start a run now` / `Cancel run <id>`), the reason the assistant gave, and **Confirm** and **Dismiss**. Confirm shows the resulting run id and status. A 409 or 503 from confirm renders inline on the card, and the card stays confirmable when the failure is retryable.
- [ ] **Run header:** the page header shows whether a run is in progress, using `optimizationQueryOptions()`; a running job disables nothing in the chat, because chat and job are separate.
- [ ] **Reconnect:** if a turn was running when the tab closed, reopening the thread calls `resumeTurn` and appends the tail; if the run is still `running`, the run panel resumes polling. A test drives `resumeTurn` with a stored partial message and asserts the text appears once, not twice.
- [ ] **Accessibility:** the composer has a labelled textarea, the streamed region is `aria-live="polite"`, the stop and confirm controls are real buttons with accessible names, focus returns to the composer after a turn, and the thread list is a labelled list. `web/src/test/admin-chat-page.test.tsx` asserts the composer label, the stop button, the denied page for a member, an action card's accessible name, and the “no threads yet” empty state.
- [ ] `bun run typecheck`, `bun run lint`, and `bun run test` pass. The existing Ask screen tests (`web/src/test/pages.test.tsx`, `chat-page`) still pass unchanged.
- [ ] Verify in browser: an admin sees a streamed reply, a step row, a stop that leaves partial text, a retry, and an action card that starts a run; a member opening `/admin/chat` sees the denied page and no composer. Recorded in the verification note with the commands used.

### CHAT-011: Chat tracing in MLflow

**Description:** As the operator, I want each chat turn to be a trace, so a bad answer can be inspected without reading container logs.

**Acceptance Criteria:**

- [ ] Boot installs `hermes-otel` and `deepfix-run-tag` on the chat profile too, with its own `hermes_otel.yaml` inside `profiles/chat/`, `content_capture: "off"`, `capture_conversation_history: false`, `capture_sender_id: false`, and the MLflow backend aimed at `MLFLOW_TRACKING_URI`.
- [ ] The experiment name comes from `MLFLOW_HERMES_CHAT_EXPERIMENT` (default `deepfix_hermes_chat`) and is created at boot when MLflow answers, the same way `deepfix_hermes` is created today. The optimization profile keeps `deepfix_hermes`.
- [ ] `deepfix-run-tag` learns a second label. The server prompt contains the exact sentence `The DeepFix chat thread id is <thread_id> and the turn id is <turn_id>.` The plugin sets `deepfix.chat_thread_id` and `deepfix.chat_turn_id` on spans, and keeps `deepfix.run_id` working for optimization rounds. Its secret scrubbing is unchanged.
- [ ] Tests: a scripted turn run through the plugin hooks (as `tasks/prd-hermes-optimizer.md` US-007 did) exports a trace whose spans carry both chat attributes and no `API_SERVER_KEY` or `HERMES_CHAT_API_SERVER_KEY`. Existing `tests/test_hermes_run_tag.py` cases still pass.
- [ ] The trace does not carry the prompt text or the reply text, because content capture stays off. The API's own SQLite store is the transcript of record.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-012: Deployed verification on Dokploy

**Description:** As an operator, I want one end-to-end pass on the deployed stack before this is called done.

**Acceptance Criteria:**

- [ ] Executed by the operator on the Dokploy deployment, not by a cloud agent, and it does not push to `dev` or `main`.
- [ ] Both profiles are up in one container. `docker port hermes` is empty; `curl` from the host to 8642 and 8643 is refused; `GET http://hermes:8643/health` succeeds from `backend-api`.
- [ ] An admin completes J1, J2, and J3 on the deployed site: a streamed answer, an action card that starts a run, the run panel moving through `running` to a terminal status, a stopped turn that keeps its partial text, a retry, and a reload that resumes.
- [ ] A member opening `/admin/chat` and calling `POST /api/admin/chat/completions` gets the denied page and 403 respectively.
- [ ] The audit table contains `turn_started`, `action_confirmed`, and either `turn_completed` or `turn_cancelled` rows for the session, and an admin can read them on the audit route.
- [ ] MLflow shows a trace in `deepfix_hermes_chat` for the session, with `deepfix.chat_thread_id` and `deepfix.chat_turn_id`, and without any key string.
- [ ] Peak container memory during a chat turn plus a run stays under the 4 GB limit; `docker stats` sample recorded. If it does not, the report says which cap to lower and no cap is raised.
- [ ] No holdout question text appears in any chat route response, the audit table, the step tail, or the MLflow trace. Verified by searching the responses for a known holdout question used in the fixture.

## Functional Requirements

Priority is **Must** (the release is incomplete without it), **Should** (planned, may slip one release), or **Could** (nice, unplanned).

| ID | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-001 | When admin chat is enabled, the `chat` Hermes profile must run on port 8643 inside the `hermes` container, with its own bearer key from `HERMES_CHAT_API_SERVER_KEY`, and must remain unreachable from outside the Compose network. With `HERMES_CHAT_ENABLED=false` the profile must not be started and every chat route must return 503. | Must | G2. The surface already exists but is stopped; the feature needs it running, still private, and switchable off without a rebuild. |
| FR-002 | The chat profile must have no `terminal`, `file`, or `code_execution` toolset, and its environment must contain no `OPENROUTER_API_KEY`, no `S3_*`, no `LLM_API_KEY`, and not the optimization `API_SERVER_KEY`. | Must | G3. A shell on the chat profile would read the optimization profile's `.env` on the shared volume. |
| FR-003 | The browser must reach the chat only through the existing same-origin `/api` proxy on the web server, and must never receive a Hermes key, an OpenRouter key, an S3 secret, or a model id from the server. | Must | G2. Stated constraint. |
| FR-004 | deepfix-api must expose `POST /admin/chat/completions`, admin-only, that accepts an OpenAI-shaped `messages` array and streams an SSE reply. A member must receive 403 and an unsigned caller 401. | Must | G1, G2. The single entry point the architecture names. |
| FR-005 | The request role enum must be `user` and `assistant` only, so a `system` or `tool` role is refused; the system prompt and the status digest must be built server-side on every turn, and the upstream conversation must come from the stored thread, not from the client's array. | Must | G3. Otherwise the browser can rewrite the policy that forbids promotion, or claim a history that never happened. |
| FR-006 | The status digest must be built from `optimization_schedule`, `champion_diff`, and `recent_runs`, must name the gate thresholds, and must contain no holdout question, holdout id, document text, private path, or credential. Agent-written fields (`reason`, `summary`) must be wrapped in explicit delimiters and labelled as untrusted data, so a run reason cannot act as an instruction. | Must | G1, G3. The answers must be about this server, must not leak, and must not let stored agent text steer the chat. |
| FR-007 | The proxy must forward to `HERMES_CHAT_BASE_URL` (default `http://hermes:8643`) with the chat bearer, `stream: true`, the server system message, and the conversation read from the thread's stored messages, ending with the request's final user message; it must relay SSE frames in order, including `: keepalive` comments, without buffering the whole reply. | Must | G4. A buffered relay would look dead for minutes. |
| FR-008 | A browser abort must mark the turn `cancelled`, keep the partial assistant text, close the upstream request, and must not cancel an optimization run. | Must | G4. Stop means stop the reply; the job has its own cancel. |
| FR-009 | A retry must create a new turn linked by `retry_of` and stream a fresh reply. | Must | G4. A failed turn must be recoverable without retyping. |
| FR-010 | Threads, turns, messages, and actions must persist in the API's SQLite database, scoped to the admin who created them, and survive an API restart. | Must | G4. Reload and reconnect depend on it. |
| FR-011 | `GET /admin/chat/turns/{turn_id}/stream` must replay the stored partial text and then tail a live turn, or return the stored terminal status after a restart. | Must | G4. Reload must not lose a reply in progress. |
| FR-012 | A turn left `running` by a restart must be marked `failed` with the interruption reason at API startup. | Must | G4. No row may stay `running` forever; mirrors `recover_running_runs`. |
| FR-013 | A completed reply must be scanned for `deepfix-action` blocks, and only `start_optimization_run` and `cancel_optimization_run` are recognised; an unknown kind is dropped and counted, not surfaced as an error. | Must | G3. The allowlist is the whole safety property of action cards. |
| FR-014 | `POST /admin/chat/actions/{action_id}/confirm` must be admin-only, single-use, owner-scoped, and expire after `ADMIN_CHAT_ACTION_TTL_SECONDS`; it must call the same domain functions the Optimization screen calls and return `OptimizationRunResponse`. | Must | G3. The admin's explicit confirmation is the only write path. |
| FR-015 | No chat code path may call `apply_candidate`, write `configs/champion.yaml` or `indexes/current`, or read holdout. | Must | G3. Stated constraint; the gate is not negotiable. |
| FR-016 | The run-to-thread link must persist in a `admin_chat_thread_runs` join table, and `GET /admin/chat/threads/{thread_id}/runs` must return each linked run's status, reason, deltas, MLflow link, and a redacted step tail capped at `ADMIN_CHAT_STEP_TAIL` lines, and must call `fail_stale_runs`. | Must | G1, G4. Progress display is what makes a multi-hour run usable, and it must survive a reload. |
| FR-017 | Rate limits must be enforced from persisted turns: per admin per window, one running turn per admin, and a global concurrent cap; a breach is 429 with `Retry-After`. | Must | G5. The container is 4 GB shared by two gateways. |
| FR-018 | Every chat-initiated action must write an audit row from the closed `kind` list, with the admin id and email, thread id, turn id, and a redacted 500-character detail; `GET /admin/chat/audit` must return them to admins only. | Must | G6. Stated constraint. |
| FR-019 | Per-turn wall clock must be bounded by `ADMIN_CHAT_TURN_TIMEOUT_SECONDS`, and request size by `ADMIN_CHAT_MAX_PROMPT_CHARS` and `ADMIN_CHAT_MAX_MESSAGES`; exceeding a bound is a readable 400 or a terminal error frame. | Must | G5. |
| FR-020 | The chat model must come from `HERMES_CHAT_MODEL`, falling back to `HERMES_MODEL`, read from the environment; an empty value must yield a 503 that names the variable, and no model literal may exist in Python or TypeScript. | Must | Stated constraint, and the repo's existing `require_model` rule. |
| FR-021 | Every error frame, audit `detail`, step tail, digest, and SSE error must pass through the existing secret redaction, extended to include `HERMES_CHAT_API_SERVER_KEY`; no response may contain `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, `OPENROUTER_API_KEY`, an S3 secret, or a path under `PRODUCT_DATA_DIR`. | Must | G2, G6. The current `_SECRET_ENVS` tuple in `optimization.py` does not list the chat key, so extending it is part of the work, not an assumption. |
| FR-022 | The chat page must stream, show history, render markdown and code, show tool and job progress, stop, retry, and render confirmable action cards. | Must | G1, G4. The named UI features. |
| FR-023 | `/admin/chat` must be in the Admin navigation group and must render a denied page for a member, with no composer and no calls to the chat routes. | Must | G3. The Admin section boundary. |
| FR-024 | A chat turn must produce an MLflow trace in `MLFLOW_HERMES_CHAT_EXPERIMENT`, tagged `deepfix.chat_thread_id` and `deepfix.chat_turn_id`, with content capture off. | Should | G6. Traceability without storing conversation text in MLflow. |
| FR-025 | The streamed reply region must be `aria-live="polite"`, the composer labelled, the stop and confirm controls named buttons, and focus must return to the composer after a turn. | Should | Accessibility; the Admin section is already keyboard usable. |
| FR-026 | The existing Ask screen, Optimization screen, and their tests must be unchanged by this feature. | Must | No regression to shipped member and admin surfaces. |

## Non-Functional Requirements

- **NFR-1 Reliability.** A chat turn must never leave `admin_chat_turns` in `running` after the API has restarted, and an optimization row must never be left `running` by a chat path (the existing `fail_stale_runs` and `recover_running_runs` rules continue to apply). A dropped browser connection is a normal end, not an exception in the API log.
- **NFR-2 Resource limits.** One optimization job at a time remains enforced by `start_optimization_run` raising `RunInProgress`. Chat adds at most `ADMIN_CHAT_MAX_CONCURRENT_TURNS` (default 2) concurrent turns and one `running` turn per admin. No story raises `mem_limit: 4g`, `cpus: "2"`, `agent.max_turns: 40`, or `OPTIMIZATION_RUN_TIMEOUT_HOURS`.
- **NFR-3 Latency.** Time to first token is a property of Hermes and OpenRouter, not of this proxy; the proxy must not add buffering, so the first byte the browser sees is the first byte Hermes sent. A thread load must be two indexed reads, not a scan: a test asserts `EXPLAIN QUERY PLAN` for the messages-by-thread and threads-by-admin queries reports an index search, and that an index exists on `admin_chat_messages (thread_id, seq)` and `admin_chat_threads (created_by, updated_at)`. The numeric target of 300 ms on the pilot data (tens of threads, hundreds of messages) is a measurement recorded in CHAT-012, not a test assertion, because a wall-clock assertion on a shared CI machine is flaky.
- **NFR-4 Security.** Every `/admin/chat/*` route requires admin, and there are exactly eleven of them: `POST /admin/chat/completions`, `GET /admin/chat/turns/{turn_id}/stream`, `GET /admin/chat/threads`, `POST /admin/chat/threads`, `GET /admin/chat/threads/{thread_id}`, `DELETE /admin/chat/threads/{thread_id}`, `GET /admin/chat/threads/{thread_id}/runs`, `POST /admin/chat/threads/{thread_id}/runs/{run_id}`, `POST /admin/chat/actions/{action_id}/confirm`, `POST /admin/chat/actions/{action_id}/reject`, and `GET /admin/chat/audit`. A test walks `app.routes` and asserts every path starting `/admin/chat` is registered with the admin dependency and that none is unauthenticated. The bearer key is sent only on the `Authorization` header to Hermes. The browser's better-auth cookie never leaves the web server (`src/api-proxy.ts` already strips `cookie` and `authorization`). No route accepts a URL that could be used to redirect the upstream call (the base URL is configuration, never request data). The chat profile keeps no terminal, so the `deepfix` shim, its secret scrub, and the read-only skill tree stay optimization-only.
- **NFR-5 Privacy.** Conversation content is stored in the API's SQLite store, which already holds feedback text and run reasons; it is not copied to S3, not returned to members, and not captured in MLflow. Holdout content is never in scope of any chat read. Audit rows hold no message text, only counts, statuses, ids, and the redacted detail.
- **NFR-6 Accessibility.** Keyboard reachable composer, stop, retry, confirm, dismiss, and thread list; visible focus; `aria-live` for streaming text; error text is announced via `role="alert"` as the existing pages do.
- **NFR-7 Compatibility.** `@assistant-ui/react` stays at `^0.15.28`; no new chat runtime package is added. The web app stays on the existing proxy and generated client; no new origin, no new server.
- **NFR-8 Operability.** The chat failure modes are visible in the API log with the same structured style as `optimization run=… event=…`: one line per turn start, per terminal state, and per refused action. The audit table is the durable record.
- **NFR-9 Compliance.** N/A for a new regulatory obligation: the pilot handles no new personal data category (an admin's email is already stored on `optimization_runs` and in Better Auth), no payment data, and no special-category data. The applicable existing obligation is data minimisation, which NFR-5 satisfies by keeping conversation text out of MLflow and out of the audit table. Revisit if a member-facing chat is ever proposed.

## Acceptance criteria (release level)

1. An admin opens `/admin/chat`, sends a message, and reads a streamed, markdown-rendered reply whose content reflects the current schedule, champion, and recent runs.
2. The reply can propose an action; only after the admin presses Confirm does a run start, and the audit log and the run row both show it.
3. The run's status, reason, deltas, MLflow link, and last step lines appear in the thread and survive a page reload; a reload during a reply resumes the reply.
4. Stop leaves the partial reply visible, stores it, and does not cancel the run.
5. Retry produces a new turn and a new reply.
6. A member can reach neither the page nor any chat route, and no member-facing screen changed.
7. No response, audit row, log line, or trace contains a Hermes key, an OpenRouter key, an S3 secret, a private path, or holdout content.
8. With the chat feature disabled (`HERMES_CHAT_ENABLED=false`), the API returns 503 on the chat routes with a readable sentence, and every other screen works exactly as before.
9. `uv run --env-file .env pytest -m "not network and not e2e"`, `uv run ruff check`, `uv run ruff format --check`, `bun run typecheck`, `bun run lint`, and `bun run test` are green, with the same known `tests/test_incremental_index.py` exception the Hermes PRD already carries if it is still open.

## Dependencies & assumptions

### Dependencies

- `tasks/prd-hermes-optimizer.md` US-001 through US-008 must be in the tree: the container, the two profiles, `start_optimization_run`, the cancel route, the step log, and `hermes_run_id`. All are implemented on `feat/hermes-optimizer` today except US-009 and US-010.
- `src/deepfix_rag/optimization.py` stays the only owner of run state, and `src/deepfix_rag/promotion.py` stays the only promoter.
- `optimization-logs/<run_id>.log` remains the step-log location and its redaction remains in force.
- The web app's `/api` proxy (`web/src/server.ts`) streams response bodies unchanged, which it does today.
- `@assistant-ui/react` and `@assistant-ui/react-markdown` stay in `web/package.json` at their current ranges.

### Assumptions (labelled)

- **(assumption)** The pinned image can start a second profile's API server on 8643 next to 8642 in one container, and the boot script's `gateway_state.json` write is the lever. CHAT-001 confirms it; if it cannot, the fallback is a second `hermes` service on the same image with `HERMES_HOME` pointed at a chat-only volume, which FR-001 already allows because it only requires a private, keyed endpoint.
- **(assumption)** `POST /v1/chat/completions` on the chat profile emits `chat.completion.chunk` frames plus `event: hermes.tool.progress` for its toolsets, as the verified facts table in the Hermes PRD records for that pin. The stream relay treats unknown frames as opaque text and does not depend on their schema.
- **(assumption)** The API keeps running one uvicorn worker (`Dockerfile` CMD has no `--workers`). The rate limit is computed from persisted rows, so it stays correct if that changes; the in-process subscriber registry for a live resume is per process, and the resume endpoint degrades to “stored text plus terminal status” when it is not the owning process.
- **(assumption)** A chat turn is short by design. Nothing in the profile allows shell work, so a 300-second cap does not cut off a legitimate operation; a task longer than that is a job, and the job has its own timeout.
- **(assumption)** Audit volume is small (tens of rows per admin per day), so an unbounded SQLite table with no retention job is acceptable for the pilot. The open question records the retention decision.
- **(assumption)** Hermes's chat completion does not need the DeepFix `run_id`; the trace correlation is the chat thread and turn id, not an optimization run id.

## Risks & mitigations

1. **A prompt-injected reply proposes an action the admin misreads.** The allowlist has two kinds, the card states the effect in one sentence, the confirm is a deliberate click, the run still passes `start_optimization_run`'s skip rules, and promotion is not an action at all. The audit row records who confirmed. Residual risk: an admin confirms a start they did not want; `cancel_optimization_run` is the remedy and nothing is promoted without the gate.
2. **The chat profile leaks the optimization profile's credentials through the shared volume.** Both profiles run as the same `hermes` user on `/opt/data`. Mitigation: the chat profile has no terminal and no file toolset, is given no `OPENROUTER_API_KEY`, `S3_*`, or product key at boot, and `skills.external_dirs` is not attached to it. CHAT-001 asserts the env split and the toolset list. Residual risk: a future toolset addition could re-open it; the test is the guard.
3. **Two gateways exhaust the 4 GB container during a run.** An optimization job already peaks near 3.9 GB on a 500-document slice (`tasks/prd-hermes-hpo-rounds.md`). Mitigation: two concurrent turns maximum, one turn per admin, no shell in the chat profile, a 300-second turn cap, and a run panel that polls the API rather than streaming from Hermes. CHAT-012 samples memory on the deployment and reports it; no cap is raised.
4. **A long reply looks dead and the admin reloads repeatedly.** Mitigation: SSE keepalives are forwarded, the composer becomes a stop button immediately, the partial text is persisted so a reload resumes, and the resume endpoint degrades safely after a restart.
5. **The proxy buffers and the stream arrives all at once.** The web server relays the body, and FastAPI's `StreamingResponse` plus `X-Accel-Buffering: no` is what prevents an intermediary from accumulating. CHAT-003 asserts the relay emits the first frame before the upstream stream closes, against a local server that deliberately delays its second chunk, so a buffering regression fails the test. CHAT-012 checks the same through the deployed proxy.
6. **Audit and transcript tables grow without limit.** Mitigation: an explicit assumption, a bounded read API, and an open question for retention. No message text is duplicated into the audit table.

## Traceability

| FR | Goal |
| :--- | :--- |
| FR-001, FR-002 | G2, G3, G5 |
| FR-003, FR-004, FR-021 | G2 |
| FR-005, FR-006 | G1, G3 |
| FR-007, FR-008, FR-009, FR-010, FR-011, FR-012 | G4 |
| FR-013, FR-014, FR-015 | G3 |
| FR-016 | G1, G4 |
| FR-017, FR-019 | G5 |
| FR-018, FR-024 | G6 |
| FR-020 | G2 |
| FR-022, FR-023, FR-025 | G1 |
| FR-026 | G1, G4 (no regression) |

No FR is unlinked to a goal, and no goal is without FRs.

## Story to FR map

| Story | FRs |
| :--- | :--- |
| CHAT-001 | FR-001, FR-002, FR-020 |
| CHAT-002 | FR-010, FR-016, FR-018 |
| CHAT-003 | FR-004, FR-005, FR-007, FR-019, FR-021 |
| CHAT-004 | FR-006 |
| CHAT-005 | FR-008, FR-009, FR-010, FR-011, FR-012 |
| CHAT-006 | FR-017, FR-018 |
| CHAT-007 | FR-013, FR-014, FR-015 |
| CHAT-008 | FR-016 |
| CHAT-009 | FR-003, FR-022 |
| CHAT-010 | FR-022, FR-023, FR-025, FR-026 |
| CHAT-011 | FR-024 |
| CHAT-012 | all, verified end to end |

## What the code does today

Written from the tree on `feat/hermes-optimizer`, 13 November 2026.

- **Chat profile exists and is stopped.** `docker/hermes/sandbox_boot.py` defines `CHAT_PROFILE = "chat"`, `CHAT_API_PORT = 8643`, `CHAT_API_KEY_ENV = "HERMES_CHAT_API_SERVER_KEY"`, and `CHAT_API_TOOLSETS = ("skills", "web", "vision", "todo", "memory")`. `chat_profile_config` writes `approvals.mode: smart`, `terminal.timeout: 180`, and that toolset list. `apply_sandbox` writes `profiles/chat/{config.yaml,SOUL.md,.env}` and `gateway_state.json` with `{"gateway_state":"stopped"}`. The image comment says the profile is not started.
- **Chat profile isolation today.** The chat `.env` is written with `replace=True` and holds only `API_SERVER_ENABLED`, `API_SERVER_HOST`, `API_SERVER_PORT`, and the chat key. `tests/test_hermes_sandbox.py::test_chat_profile_env_is_replaced` asserts no `OPENROUTER_API_KEY`, no `S3_`, and no optimization key. `_install_trace_plugins` runs on the optimization home only, and `write_eval_env` and `mlflow_env` are optimization-only.
- **Optimization profile.** 8642, `API_SERVER_KEY` of at least 16 characters, `agent.max_turns: 40`, toolsets `terminal`, `file`, `skills`. `src/deepfix_rag/hermes_client.py` holds `HermesClient` (base URL + key), `optimization_instructions(run_id)`, `start_run`, `get_run`, `stop_run`, `iter_events`, and the SSE parser with `: keepalive` handling, redaction, and `HERMES_MAX_TURNS = 40`.
- **API.** `src/deepfix_rag/api/app.py` builds the FastAPI app, adds CORS for `WEB_ORIGIN`, mounts seven routers, calls `recover_running_runs` and starts the feedback worker and the optimization scheduler in its lifespan. `src/deepfix_rag/api/deps.py` has `current_user` (Better Auth bearer, JWKS) and `require_admin`. `src/deepfix_rag/api/routes/optimization.py` has `get_optimization_status`, `update_optimization_schedule`, `start_optimization_run`, `cancel_optimization_run` with operation ids and shared error responses. There is no streaming route and no SSE helper anywhere in the API today.
- **Run state.** `src/deepfix_rag/optimization.py` owns `start_optimization_run` (skip rules, `RunInProgress`, `AgentUnavailable`), `cancel_optimization_run`, `finish_run`, `fail_interrupted`, `fail_stale_runs`, `recover_running_runs`, `recent_runs`, `_write_step`/`_step_line`, and `_append_log` to `{PRODUCT_DATA_DIR}/optimization-logs/<run_id>.log`. `_redact` and `_secrets` are private to that module.
- **Promotion.** `src/deepfix_rag/promotion.py::apply_candidate` validates, scores dev and holdout, and only then commits. Nothing else writes the champion.
- **Storage.** `src/deepfix_rag/storage.py::connect` is the shared SQLite context manager; tables are created by `init_*` functions per module (`init_documents`, `init_optimization`). `drop_account_tables` runs at startup.
- **Web.** TanStack Start. `src/routes/admin.tsx` is the admin route pattern (loader returns `null` for a non-admin, then the component renders `OptimizationDenied`). `src/components/layout/navigation.ts` gates `/history` and `/admin` by role via `ADMIN_PATHS`. `src/services/optimization.functions.ts` shows the `requireRole("admin", …)` server-function pattern. `src/server.ts` proxies `/api/*` to FastAPI, strips `cookie` and `authorization`, adds the identity bearer, and streams `upstream.body` back. `src/features/chat/chat-page.tsx` already uses `AssistantRuntimeProvider` with `useExternalStoreRuntime` and `ThreadPrimitive`, keeping threads in `localStorage` under per-user keys.
- **Dependencies.** `web/package.json` has `@assistant-ui/react ^0.15.28`, `@assistant-ui/react-markdown ^0.14.22`, `react-markdown ^10`, and `remark-gfm ^4`. There is no `@assistant-ui/react-data-stream` and no `ai` package. `web/node_modules` is not installed in this checkout.
- **Absent entirely.** No rate limiter, no audit table, no chat table, no SSE route, no admin chat route, no chat UI, and no test for any of them. `grep` for `rate_limit` and `audit` over `src/` finds nothing. `_SECRET_ENVS` in `optimization.py` does not list `HERMES_CHAT_API_SERVER_KEY`, so redaction of the chat key is new work (FR-021).

## Design Considerations

### Why a proxy route and not Hermes's own auth

Hermes authenticates with one static bearer key per profile. That key cannot be handed to a browser, cannot be scoped to an admin, and produces no per-admin record. The proxy route is where the admin identity, the rate limit, the audit row, and the redaction live. It also means the system prompt cannot be replaced by the client, which is the property that keeps promotion out of reach.

### What the two planes are

- **Conversation plane.** `POST /admin/chat/completions` and its resume endpoint. Request-scoped, bounded, cancellable, and never writes run state.
- **Job plane.** The existing `optimization_runs` lifecycle, reached only through a confirmed action card. Multi-hour, owned by the scheduler thread, polled by the UI.

Mixing them is the main design hazard: a chat request must not be held open for a three-hour run (the Hermes PRD rejected exactly that shape), and a cancelled chat turn must not be read as a cancelled job. The two ids are separate (`turn_id` versus `run_id`) and the audit rows name both when they meet.

### How the assistant proposes an action

The reply may contain:

````
```deepfix-action
{"kind": "start_optimization_run", "reason": "The last three runs were skipped for too few new labels; a run now would still measure the champion."}
```
````

The API extracts it after the turn ends, validates it against the two allowed kinds, stores it with a 10-minute expiry, and returns it in the terminal `deepfix.turn` frame. The UI renders a card. Only a click on Confirm reaches `POST /admin/chat/actions/{id}/confirm`, which re-checks the owner, the status, and the expiry before calling the same function Run now calls. If the model never emits a block, nothing is lost: the Optimization screen and its Run now / Cancel run buttons are unchanged, and the chat page header still shows the current run.

### Memory and the 4 GB envelope

The optimization job is the expensive tenant. Chat is cheap by comparison but not free: each turn holds one model stream, and a second gateway adds a process. The caps (`ADMIN_CHAT_MAX_CONCURRENT_TURNS` 2, one running turn per admin, 300 seconds per turn, a 48 000-character prompt) are chosen to sit under a job's footprint, not beside it. If CHAT-012 shows a run and two turns cannot coexist, the first lever is the global concurrent cap to 1, not the container limit.

### Test plan

- `uv run --env-file .env pytest -v -m "not network and not e2e"` for everything on the API side, including a real local HTTP server standing in for the chat profile (no `unittest.mock`, matching `AGENTS.md`), `tmp_path` SQLite, and a temp step log.
- `uv run ruff check` and `uv run ruff format --check`.
- `bun run typecheck`, `bun run lint`, `bun run test` in `web/`, including new `admin-chat-stream.test.ts` and `admin-chat-page.test.tsx`.
- Browser verification for CHAT-010 on the local stack (API, web, and a stub chat server with the pinned image's frame shape).
- CHAT-012 on Dokploy, by the operator.

### Phased milestones

| Phase | Stories | Done when |
| :--- | :--- | :--- |
| 1. Server surface | CHAT-001, CHAT-002 | The chat profile answers on 8643 and the tables exist. |
| 2. Conversation | CHAT-003, CHAT-004, CHAT-005 | A turn streams through the API against a local stand-in and survives a reload. |
| 3. Safety and jobs | CHAT-006, CHAT-007, CHAT-008 | Limits, audit, action cards, and the run panel work with real domain calls. |
| 4. UI | CHAT-009, CHAT-010 | The admin chats in the browser; a member is denied. |
| 5. Evidence | CHAT-011, CHAT-012 | A trace exists in `deepfix_hermes_chat`, and the deployed pass is recorded. |

## Success Metrics

- An admin answers “why did the last run reject?” in the chat without opening MLflow, in one turn.
- An admin starts a run from an action card, watches it to a terminal status in the thread, and reloads the page mid-run without losing the panel.
- Zero credentials in any browser payload, verified by a test that greps the web bundle sources and by a manual network inspection in CHAT-012.
- Zero holdout content in any chat response, audit row, step tail, or trace, verified by a search for a known holdout question.
- A stopped turn keeps its partial text; a retried turn produces a new reply; a resumed turn shows no duplicated text.
- The 31st turn in a five-minute window is refused with a readable sentence.
- Peak container memory during chat plus a run stays under 4 GB.
- The Optimization screen and the Ask screen behave exactly as before, with their existing tests unchanged and green.

## PRD quality gates (self-check)

- [x] Every **Must** FR has testable acceptance wording. Each names a route, a setting, a status code, a frame, or a visible control.
- [x] No duplicate FRs. FR-021 (redaction) and FR-006 (digest content) are adjacent but distinct: one is about every outbound string, the other about what enters the prompt.
- [x] Non-goals and Won't do not contradict a Must FR. The chat proposes and confirms; the promotion FR-015 forbids the promote path, and no Must FR requires it.
- [x] Nouns are glossary-consistent: thread, turn, job/run, action card, step line, gate, holdout, chat profile.
- [x] Security and privacy are covered (NFR-4, NFR-5, FR-002, FR-003, FR-015, FR-021) because this feature carries authn, credentials, and a data boundary.
- [x] Every FR maps to a goal in Traceability, and each is reachable from a story in the Story to FR map.

## Validation (validate-prd)

Run on this document before it was committed, using the `validate-prd` dimensions and report template. Verdict: **Pass with actions**, and every action below was applied in place.

| Dimension | Status | Evidence |
| :--- | :--- | :--- |
| Completeness | Green | Overview, goals, non-goals (three levels), personas, journeys, 12 stories, 26 FRs, 9 NFRs, release acceptance, dependencies, 6 risks, 8 open questions, glossary, traceability. |
| Consistency | Green | Glossary terms match FR wording; the two planes (conversation, job) are used consistently; no FR contradicts “Won't”. |
| Testability | Green after fixes | Each Must FR names a route, setting, status code, frame, or visible control. Two vague spots were fixed: NFR-3 had a wall-clock 300 ms target that no CI machine can assert, and FR-016's step tail had no read bound. |
| Traceability | Green | Every FR maps to a goal; every FR appears in the Story to FR map; every goal has FRs. |
| Feasibility | Amber | Depends on starting a second gateway in one 4 GB container, which no one has measured on the target VPS. Recorded as A1 and open question 8 rather than claimed as solved. |
| Security & privacy | Green after fixes | Admin-only on all eleven routes, credentials server-side, redaction on every outbound string, holdout excluded, audit trail. Three gaps were fixed: prompt injection through agent-written `reason`/`summary` text, rate-limit bypass by client-supplied history, and a chat key missing from the redaction list. |

Findings raised and fixed:

1. **Blocker — a Must requirement was untestable.** NFR-3 asserted a 300 ms thread load. Replaced with an index/query-plan assertion, and the timing became a CHAT-012 measurement.
2. **Should-fix — the client could rewrite the conversation.** `messages` was accepted from the browser and forwarded. FR-005 and CHAT-003 now build the upstream conversation from stored messages and refuse a mismatched history.
3. **Should-fix — prompt injection through stored agent text.** `reason` and `summary` reach the digest verbatim. FR-006 now requires them delimited and labelled untrusted, with a CHAT-004 test.
4. **Should-fix — an unbounded step-log read.** CHAT-008 now reads at most 65 536 bytes from the tail and has a 2 MB test.
5. **Should-fix — a route-count claim that did not match the routes.** NFR-4 now lists all eleven and adds a test that walks `app.routes`.
6. **Should-fix — two story cross-references were wrong.** The action-card block was cited as CHAT-005 in CHAT-004 and is CHAT-007; the operator story was called CHAT-010 in the preamble. Both corrected.
7. **Should-fix — missing index definitions.** A rate limit and a 5-second poll need an index; CHAT-002 now names five.
8. **Should-fix — a retry field with two meanings.** `turn_id` in the request was overloaded; it is now `retry_of`, validated against the same thread.
9. **Nice-to-have — the run-to-thread link had no table.** Added `admin_chat_thread_runs` to CHAT-002 and FR-016.
10. **Nice-to-have — the flat route filename would have nested under `admin.tsx`.** Changed to `admin_.chat.tsx`, because `admin.tsx` renders no `<Outlet />`.
11. **Nice-to-have — compliance was unstated.** Added NFR-9, marked N/A with the reason.
12. **Nice-to-have — “user-facing success” had no accessibility angle.** G1 and FR-025 now name it.
13. **Consistency — CHAT-011 contradicted a stated fact.** `tasks/prd-hermes-optimizer.md` US-007 says the chat profile does not get the trace plugins; CHAT-011 gives it its own. Rather than silently override, an “Amendments required” section records the five places that document and this one touch, in the style of `tasks/prd-hermes-hpo-rounds.md`.
14. **Correctness — the sidebar would double-highlight.** `AppSidebar` uses `active.startsWith(item.to)`, so `/admin/chat` highlights both “Optimization” and “Hermes chat”. CHAT-010 now requires longest-prefix matching and a two-route test.
15. **Security — the chat key is not in the redaction list.** `optimization._SECRET_ENVS` omits `HERMES_CHAT_API_SERVER_KEY`, so a naive reuse of the helper would leak it into an error frame. FR-021 and CHAT-002 now require adding it, with a test.

Remaining Amber, carried deliberately: the second-gateway feasibility (open question 8) and the disconnect semantics (open question 1). Both are resolved by a measurement, not by more editing.

Next step after this PRD is accepted: **`create-story`** for CHAT-001, unless open question 8 is answered “no”, which would collapse the design to the opt-in variant and need only `HERMES_CHAT_ENABLED` defaults changed.

## Open questions / assumptions

Decisions needed from Fadel (open questions):

1. **Does Hermes abort an in-flight chat turn when the client disconnects?** The runs API has `POST /v1/runs/{run_id}/stop`; the chat completions surface documents no stop route. The proxy will close the upstream stream, record `cancelled`, and discard the rest. If the pinned image keeps the turn running, it costs model tokens and one of the concurrency slots until it finishes. Owner: whoever runs CHAT-001; the answer is recorded there and can change FR-008 to a stop call if one exists. **(assumption: closing the stream is enough.)**
2. **Should the chat profile start by default (`HERMES_CHAT_ENABLED=true`) or be opt-in per deployment?** This PRD assumes on, so `/admin/chat` works after a normal deploy. Off costs one env var and a 503.
3. **Audit and transcript retention.** Assumed unbounded for the pilot, with bounded reads. Fadel should decide whether audit rows are kept forever (they are small) and whether deleting a thread should keep the transcript under a soft-delete flag for incident review.
4. **Should the chat be able to read one run's step log on demand** (a “show me the last 200 lines” tool) instead of the 50-line tail? The tail is in this PRD; a paged read is a small addition but another route.
5. **Which experiment name is final for chat traces:** `deepfix_hermes_chat` (assumed) or one experiment with a `deepfix.surface` attribute? The attribute route keeps one place to look; the separate experiment keeps the optimization view clean.
6. **Is `HERMES_CHAT_MODEL` allowed to differ from `HERMES_MODEL` in the pilot?** The architecture says models come from env, so the variable exists; the default falls back to `HERMES_MODEL`. This PRD assumes they stay equal until someone measures the cost of a stronger chat model.
7. **Should a confirmed `start_optimization_run` be allowed while a chat turn is still streaming?** Assumed yes: they are separate planes and the run has its own guard. The alternative is to queue the confirm until the turn ends.
8. **Is a second admin chat profile gateway acceptable at all on a 7.6 GB VPS?** The brief allows it and the caps are sized for it, but nobody has measured two gateways plus a run. CHAT-012 answers it with `docker stats`; if the answer is no, the chat profile becomes opt-in per deployment and `HERMES_CHAT_ENABLED` defaults to `false`.

Assumptions carried, each also labelled where it matters in “Dependencies & assumptions”:

- A1. The pinned image can host both profile gateways in one container, and the boot script's `gateway_state.json` is the lever.
- A2. The chat profile speaks `chat.completion.chunk` plus `hermes.tool.progress`; unknown frames are relayed as opaque text.
- A3. One uvicorn worker, so an in-process resume registry is sufficient; the resume endpoint degrades to stored text otherwise.
- A4. A 300-second turn cap is far above a legitimate chat turn, because the chat profile cannot run shell work.
- A5. Audit and transcript volume is small enough for unbounded SQLite in the pilot.
- A6. Chat correlation is the thread and turn id, not an optimization `run_id`.

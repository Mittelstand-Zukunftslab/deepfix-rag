# PRD: Admin Hermes Chat

## Introduction

The Admin section of the DeepFix web app today shows the nightly optimization schedule, the champion diff, and a table of recent runs. An admin can press **Run now** and **Cancel run**, and nothing else. Everything the agent learned during a round — why it rejected a candidate, what an HPO sweep sampled, which metric moved — is only readable by opening the run's MLflow trace or the API-private step log. Turning that into a decision takes a CLI session and an operator.

`tasks/prd-hermes-optimizer.md` already reserves the surface for this: a second Hermes profile named `chat`, on port 8643, with no terminal and no file-write toolset, proxied by the API and never exposed (`FR-15` and “Design Considerations → Admin chat profile” in that document). That profile is written at boot and left stopped (`docker/hermes/sandbox_boot.py` writes `gateway_state.json` with `gateway_state: stopped`). This feature builds the chat on top of it, and keeps “off” as the default: `HERMES_CHAT_ENABLED` is new and defaults to `false` (D-1).

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
hermes container, chat profile gateway, http://hermes:8643
  |  Authorization: Bearer <HERMES_CHAT_API_SERVER_KEY>
  v
OpenRouter via HERMES_CHAT_MODEL, falling back to HERMES_MODEL
```

Everything on the left of the Hermes arrow is DeepFix. The browser never holds an OpenRouter key, a Hermes key, or a model id. Hermes is never reachable from outside the Compose network.

The port and the auth are confirmed from the tree, not assumed: `docker/hermes/sandbox_boot.py` sets `CHAT_API_PORT = 8643` and `CHAT_API_KEY_ENV = "HERMES_CHAT_API_SERVER_KEY"` and writes both `API_SERVER_PORT=8643` and `API_SERVER_KEY=<chat key>` into `profiles/chat/.env`; `compose.hermes.yml` passes `HERMES_CHAT_API_SERVER_KEY` from the environment and publishes no ports. The optimization profile keeps 8642 with the separate `API_SERVER_KEY`.

**The chat gateway must be a standalone profile, not a multiplexed one.** Hermes v0.22.7 enables `gateway.multiplex_profiles` by default, and a multiplexed gateway serves *every* live profile under `profiles/` through a `/p/<profile>/v1/...` path on the default listener — in this case the optimization gateway's own process on 8642. That would make the chat profile cost memory even when chat is switched off, and it would tie chat availability to the optimization gateway. So `chat` sets `gateway.standalone: true` in its own `config.yaml`: the host does not enumerate it, and it runs its own listener on 8643 only while its own gateway runs. This is read from the provider docs, not measured on the pinned image, so CHAT-001 verifies it (see Decision D-1).

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

## Decisions

Fadel answered the open questions from the first draft on 2026-11-13, and settled the last two (audit deletion and retention) on 2026-11-14. These six are settled and the stories assume them. Each records what it replaces, so a reader can see the draft it supersedes.

### D-1: The chat gateway runs only while chat is enabled, and costs no memory when it is off

`HERMES_CHAT_ENABLED` defaults to **`false`**. Chat is opt-in per deployment.

- **Enabled:** the chat profile is a **standalone** gateway (`gateway.standalone: true`) on its own listener at 8643, started by boot. It does not ride the optimization gateway, and the optimization gateway does not serve it (see the architecture note above).
- **Disabled:** the chat profile's gateway is not started at all — no listener on 8643, no gateway process, no resident memory. Boot writes `gateway_state.json` as `stopped` exactly as it does today. (US-001 of the Hermes PRD records the reconciler logging `profile=chat prior_state=stopped action=registered`, so a bookkeeping slot entry exists today and is not what costs memory; what must be absent is a *running* gateway process. CHAT-001 records whether that log line changes when chat is enabled.) Every `/admin/chat/*` route returns 503 with the disabled sentence **except `GET /admin/chat/audit` and `DELETE /admin/chat/threads/{id}`, which stay available** (D-4), and the UI renders a disabled state (D-2).
- **Also disabled:** the chat profile must be `standalone` even when it is off, so a multiplexing default can never quietly serve it. Boot refuses to start if `gateway.standalone` is missing from the profile's config, which makes the guarantee a boot-time check rather than a convention.
- CHAT-001 and CHAT-012 check both states, so the “no memory” claim is a measurement rather than an assertion.

What “no memory” means precisely, because the loose phrasing is not checkable: it means **no chat gateway process exists and nothing listens on 8643**. It does not mean the container's RSS drops to zero or that the profile's files disappear. So the check is the falsifiable pair:

1. Exactly one Hermes gateway process runs in the container, and it serves the optimizer's profile (matched on process arguments and profile, not a literal name — the pin runs the gateway through a Python interpreter).
2. Nothing is bound to 8643.

CHAT-001 and CHAT-012 assert those two, and record container RSS with chat on and off **for information only** — summed RSS double-counts shared pages between two gateways in one container, so a delta is a trend, not a guarantee. The guarantee is process and port absence. The 4 GB `mem_limit` is unchanged, the optimization gateway is unaffected, and the profile's files stay on disk (a few kilobytes of `config.yaml`, `.env`, `SOUL.md`) — “no memory” refers to a running gateway, not to disk.

### D-2: Chat is opt-in, and the UI says so plainly

With `HERMES_CHAT_ENABLED` unset or `false`:

- The `/admin/chat` page renders an explicit disabled state with the heading “Hermes chat is turned off on this server.” and one sentence of how to turn it on (`HERMES_CHAT_ENABLED=true`, operator action). There is no composer, no thread list, no retry, and no request to any `/admin/chat/*` route on load.
- The sidebar still shows the “Hermes chat” entry for an admin, so the feature is discoverable, and the page explains itself. Hiding the entry would leave an admin who read this PRD wondering whether the deploy worked.
- The API's 503 carries `code: "chat_disabled"` and the same sentence, so a stale open tab and a fresh load agree.
- A test asserts the page makes exactly zero chat route calls in the disabled state, which is both a UX and a load property: a disabled deployment must not poll.

### D-3: `HERMES_CHAT_MODEL` is optional and falls back to `HERMES_MODEL`

`HERMES_CHAT_MODEL` may be unset or blank; the chat profile then uses `HERMES_MODEL`. This is the behaviour the first draft already described as an assumption; it is now the decision. Consequences:

- `example.env` ships `HERMES_CHAT_MODEL` **commented out**, so a default deploy runs chat on the same model as the optimizer and cannot drift silently.
- Setting it to a different id is supported and is an operator choice, not a bug. It is the one place where a second model id is legitimate, and it is still environment-only.
- If both are empty, chat is misconfigured, not disabled: the route returns 503 naming `HERMES_CHAT_MODEL` and `HERMES_MODEL`, distinct from the D-2 disabled sentence.
- No model literal exists in Python or TypeScript (unchanged FR-020).

### D-4: Thread delete is a hard delete; audit rows survive, without message content

`DELETE /admin/chat/threads/{thread_id}` **hard-deletes** the thread row, its action cards, its messages, its turns, and its thread-run links, in one transaction, children before parent. Deleting a thread twice is idempotent (204 both times).

Audit rows **are kept**. Recommendation, which this PRD adopts: keep a minimal audit trail with no message content, because the audit row is the security record of who started or cancelled an optimization, and that record must outlive the conversation that caused it.

- An audit row keeps `event_id`, `at`, `admin_id`, `admin_email`, `thread_id`, `turn_id`, `kind`, and `detail` (redacted, 500 characters, already free of message text by FR-018).
- After a thread delete, the surviving rows reference a `thread_id` that no longer exists. That is deliberate: the audit table has no foreign key to `admin_chat_threads`, so a delete cannot cascade, and `thread_id` stays a plain string. The audit route returns them with every stored field intact.
- The audit route marks rows whose thread is gone with `thread_deleted: true`, so a reader knows the transcript behind an event is no longer available rather than assuming it was never stored. That flag is the only difference from a live thread's rows.
- `turn_id` also survives the delete. `admin_chat_turns` is hard-deleted, so a `turn_id` in an audit row after a delete is not resolvable — the same `thread_deleted` flag covers it.
- One extra audit `kind` is added for this: `thread_deleted`, written before the rows are removed, with the deleted thread id and counts in `detail`. The deletion becomes its own auditable event.

**Settled by Fadel on 2026-11-14: a thread delete never deletes audit rows.** The stricter variant flagged in the first draft (delete the audit rows too) is rejected. The trade-off it avoids is real and accepted: a “delete” does not fully erase everything the admin did, because the record of who started or cancelled a run must outlive the conversation. What eventually bounds that record is retention, not deletion (D-6). D-4's tests pin the behaviour: `delete_thread` writes `thread_deleted` and removes no `admin_chat_audit` row.

Two consequences that follow from keeping the rows, and are part of the decision rather than left to the implementer:

- **Two routes escape the disabled 503: audit and delete.** Turning chat off must not hide what chat did (audit), and must not strand conversation text on disk that the admin can no longer remove (delete). `GET /admin/chat/audit` and `DELETE /admin/chat/threads/{thread_id}` keep answering with admin auth while every other chat route returns the disabled 503 (CHAT-003, CHAT-006). Both are cheap, neither touches Hermes, and both exist to serve the very data chat left behind.
- **Deletion is children-before-parent.** The order is action cards, then messages, then turns, then `admin_chat_thread_runs`, then the thread row, so no intermediate state has an orphan child pointing at a live parent. `optimization_runs` rows never go.

### D-5: Chat traces go to a separate `deepfix_hermes_chat` experiment

Confirmed, not assumed: chat spans go to their own MLflow experiment, named by `MLFLOW_HERMES_CHAT_EXPERIMENT` (default `deepfix_hermes_chat`). The optimization profile keeps `deepfix_hermes`. CHAT-011 already specified this; it is now a decision rather than an assumption, so open question 5 is closed.

### D-6: Audit rows are pruned after 14 days, by a setting, whether chat is on or off

Fadel settled the retention question on 2026-11-14: audit rows older than **14 days** are deleted. D-4 says a delete never removes one, and “keep the security record” is not the same as “keep it indefinitely”, so the time bound is now explicit and the only thing that removes an audit row is age.

- The window is a setting, **`ADMIN_CHAT_AUDIT_RETENTION_DAYS`**, default **14**, plumbed like every other chat setting (CHAT-001's `Settings` field, the `backend-api` Compose environment with a non-empty default `${ADMIN_CHAT_AUDIT_RETENTION_DAYS:-14}`, `example.env` with a one-line comment, and the plumbing test). No literal 14 exists in Python; a test that changes the setting changes what is pruned.
- **`0` or a negative value disables pruning** and is the only way to keep rows forever. It is a deliberate operator choice, documented in `example.env`, not a silent default. This is also what makes the prune testable both ways without waiting 14 days.
- The prune is a plain SQLite `DELETE` on `admin_chat_audit` for rows whose `at` is older than the window, run by the API itself. It is not a chat feature and it touches no Hermes endpoint, so **it keeps running when `HERMES_CHAT_ENABLED=false`** — the audit route is one of the two routes D-4 keeps reachable when chat is off, and a table that only ages out while chat is on would grow without bound on exactly the deployments that stopped using chat.
  - **Where it runs: the existing optimization scheduler tick.** The prune is at most once per hour per process, guarded by a last-run timestamp held in memory, and it runs before the due-run check so a slow prune can never delay a run. It is not a new thread, not a new process, and not a Celery-style job: the API is one uvicorn worker (A4), and a background thread for a once-a-day `DELETE` would be a second lifecycle to get wrong.
    - One change to the loop is required and is part of this decision. Today it waits until the *next scheduled run*: `delay = 3600.0` only when the schedule is paused, and otherwise the whole gap to the next run, which with the default 02:00 cron is up to 24 hours. The wait becomes `min(seconds_until_next_run, 3600)` in every case, so the thread wakes at least hourly, keeps the existing paused-schedule behaviour, and a tick with nothing due does nothing. Without that cap a prune tied to the tick would effectively run once a day at whatever hour the run happens.
  - Rejected: `fail_stale_runs` (a request-path function, so a `DELETE` would run on every status poll), a startup-only prune (an API that is never restarted never prunes), and a separate retention thread (a second daemon to start, stop, and test).
  - If the scheduler is later removed, the prune moves with it as one function call in `app.py`'s lifespan; that is a one-line move precisely because the function lives in `admin_chat` and takes `now` as an argument.
- The prune is **not audited**. Writing an audit row for a prune would itself be a row that ages out, and the prune's own log line (one structured line with the deleted count) is the operational record.
- `ADMIN_CHAT_AUDIT_RETENTION_DAYS` is read on each prune, not cached at boot, so an operator changing it and restarting the API takes effect without a code change.
- Rows are pruned by `at`, which every row has (`admin_chat_audit.at`, written by `record_audit`). Time is compared as stored ISO-8601 UTC, the same format `_now()` writes elsewhere in the repo.

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
- **Chat profile** — the Hermes profile named `chat`. Own home, own `config.yaml`, own `.env`, own bearer key, and a **standalone** gateway on port 8643 that runs only while `HERMES_CHAT_ENABLED` is true. Optimization uses the default profile on 8642.
- **Disabled state** — the `/admin/chat` presentation when `HERMES_CHAT_ENABLED` is false: an explanatory page, no composer, no chat route calls.
- **Thread delete** — a hard delete of the thread, its turns, its messages, and its action cards. Audit rows survive, without message content, until the retention window removes them (D-4, D-6).
- **Retention window** — `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (default 14). An audit row older than it is deleted by the prune; `0` disables the prune (D-6).
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
- **G5** Stay inside the server envelope: 4 GB and 2 CPUs for Hermes, one optimization at a time, and a hard bound on chat concurrency and turn length — with chat off by default, so a deployment that does not want it pays no memory at all.
- **G6** Make every chat-initiated action traceable: an MLflow trace with the thread and turn id, and an append-only audit log in the API's SQLite database.

## Scope

### In scope

- Starting the `chat` Hermes profile as a standalone gateway, gated on `HERMES_CHAT_ENABLED`, so it costs nothing while chat is off.
- A new domain module and SQLite tables for threads, turns, messages, action cards, and audit events.
- One new admin-only streaming route and a small set of admin-only JSON routes on deepfix-api.
- A resumable per-turn stream, so a reload does not lose the reply in progress.
- Run progress for a thread, read from `optimization_runs` and the existing step log.
- An admin-only `/admin/chat` route in the web app with an explicit disabled state, built with `@assistant-ui/react`.
- Tracing for the chat profile in the `deepfix_hermes_chat` MLflow experiment, tagged with the thread and turn id.

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

1. Admin opens `/admin/chat`. If chat is disabled (`HERMES_CHAT_ENABLED` false), the page stops here with the disabled state from D-2 and makes no request to any chat route. Otherwise it loads the thread list, newest first, and opens the most recent thread, or an empty state.
2. The header shows the current run status: idle, or “Running since 02:00”, or “Last run rejected: …”.
3. Admin types “why did the last run reject?”. The composer sends `POST /api/admin/chat/completions` with the thread's messages.
4. deepfix-api checks the admin role, counts the admin's recent turns, writes `turn_started` to the audit log, prepends the server-side system prompt and a status digest, and forwards to `http://hermes:8643/v1/chat/completions` with `stream: true`.
5. Assistant text arrives as `chat.completion.chunk` frames. The UI renders markdown as it arrives, scrolls with the viewport, and shows a stop button in place of send.
6. When the turn ends, the assistant message is stored, the thread's `updated_at` moves, and any action card the reply proposed appears below the message.

### J2 — Start a run, watch it, come back later (FR-013, FR-014, FR-016, FR-018)

1. The reply proposes “Start a nightly run now”. An action card renders with the run's preconditions (dev count, new dev count, holdout count) read from `GET /admin/optimization`.
2. Admin presses **Confirm**. The browser calls the same-origin `/api/admin/chat/actions/{action_id}/confirm`. deepfix-api checks the action belongs to this admin and this thread, is unconfirmed and unexpired, writes `action_confirmed`, and calls the same domain function the Optimization screen's Run now uses.
3. The response is the existing `OptimizationRunResponse`. The thread now shows a run panel: status, start time, and the last step lines.
4. Admin closes the tab. Ten minutes later they reopen the thread: the run panel re-reads the run, still `running`, and keeps polling every 5 seconds while the row is `running`.
5. The run finishes. The panel shows the final status, the reason, the dev delta, the aggregate holdout delta, and the MLflow link, exactly as the Optimization table does. No holdout rows.

### J3 — Stop a long reply, or retry it (FR-008, FR-009)

1. A turn runs past 30 seconds. Admin presses **Stop**.
2. The browser aborts its fetch. deepfix-api sees the disconnect, closes the upstream Hermes request, marks the turn `cancelled`, and writes `turn_cancelled`. The partial assistant text stays visible and is stored.
3. Admin presses the retry control on the last assistant message. The browser sends the same user message again; deepfix-api records a new turn with `retry_of` set to the cancelled one and streams the new reply.

### J4 — Delete a thread that started a run (FR-018, D-4)

1. Admin presses **Delete** on a thread in the history list and confirms in a dialog that names what goes: the conversation and its messages.
2. deepfix-api writes `thread_deleted` with the deleted thread id and the counts, then hard-deletes the thread, its turns, its messages, and its action cards in one transaction.
3. The thread disappears from the list. The run it started is untouched: `optimization_runs` rows are never deleted by chat.
4. Later, the audit view shows the surviving rows for that thread with `thread_deleted: true`, including `action_confirmed` and the `run_id` it produced. No message text is there, and none ever was.

## User stories

Stories are ordered. Each is one implementation session on `feat/hermes-optimizer`, independently testable, and committed on its own. This PRD does not check them off. `CHAT-012` is an operator session after deployment; a cloud agent cannot run it.

### CHAT-001: Start the chat profile and prove it answers

**Description:** As an operator, I want the `chat` Hermes profile to run as its own standalone gateway on port 8643 only while chat is enabled, so the API can talk to it and it costs nothing when off.

**Acceptance Criteria:**

- [ ] `HERMES_CHAT_ENABLED` is a new setting on `Settings` (`false` by default), passed to the container by `compose.hermes.yml`, and read by `docker/hermes/sandbox_boot.py`. Nothing else decides whether chat runs (D-1).
- [ ] The chat profile's `config.yaml` gets `gateway.standalone: true`, written by `chat_profile_config`. This is what stops the default multiplexing gateway (`gateway.multiplex_profiles`, on by default on this pin) from serving the chat profile under `/p/chat/...` on the optimization listener on 8642. Boot **refuses to start** (`SandboxBootError`) when the written chat config lacks that key, so the guarantee is a checked invariant rather than a convention.
- [ ] **Enabled** (`HERMES_CHAT_ENABLED=true`): boot starts the chat profile's gateway so it listens on 8643, and records in the verification note the exact command on `nousresearch/hermes-agent` v0.22.7 (`hermes -p chat gateway run` foreground vs the s6 per-profile slot; the docs also note `hermes profile create` registers an s6 slot, so record which one this image actually uses) and whether it survives `docker restart`.
- [ ] **Disabled** (unset or `false`, the default): boot does not start the chat gateway and writes `profiles/chat/gateway_state.json` as `{"gateway_state":"stopped"}` exactly as today. No process listens on 8643. A test asserts the disabled path writes `stopped` and does not invoke the start command.
- [ ] **Disabled costs no memory:** with chat off, exactly one Hermes gateway process runs in the container (the optimizer's) and nothing is bound to 8643. With chat on, a second gateway process exists for the `chat` profile and 8643 is bound. The check matches on the process arguments and profile (the pin's process line is a Python interpreter running the gateway, so a literal `hermes gateway` name match is not reliable) — for example count processes whose args contain `gateway` and distinguish them by the profile they serve. Container RSS is recorded for information in both states but is not the guarantee (summed RSS double-counts shared pages). CHAT-012 repeats the check on the deployment; this story records what it could not run on this VM.
- [ ] The chat profile's `.env` contains `API_SERVER_ENABLED=true`, `API_SERVER_HOST=0.0.0.0`, `API_SERVER_PORT=8643`, and `API_SERVER_KEY` taken from `HERMES_CHAT_API_SERVER_KEY` only when it passes `validate_api_key` (at least 16 characters, no placeholder). It contains no `OPENROUTER_API_KEY`, no `S3_*`, no `LLM_API_KEY`, and not the optimization `API_SERVER_KEY`. **`test_chat_profile_env_is_replaced` in `tests/test_hermes_sandbox.py` must change, and that change is part of this story:** it currently asserts `"API_SERVER_KEY" not in text` for the chat `.env`, which is exactly what this story reverses. The new assertions are: the chat `.env` `API_SERVER_KEY` equals `HERMES_CHAT_API_SERVER_KEY`; it does **not** equal the optimization `API_SERVER_KEY`; the optimization `.env` still carries its own key; and no `OPENROUTER_API_KEY`, `S3_`, or `LLM_API_KEY` appears in the chat file. The test's `_env()` fixture gains `HERMES_CHAT_API_SERVER_KEY` (`CHAT_KEY`, "b" * 32) so the enabled case is exercised.
- [ ] The chat profile's model comes from `HERMES_CHAT_MODEL`, falling back to `HERMES_MODEL` when unset or blank (D-3). Both are written into the chat `config.yaml` by `chat_profile_config`. A test covers the three cases: chat model set, chat model blank, chat model unset. No model literal appears in `sandbox_boot.py`, the Compose file, or the image.
- [ ] **The API container can see its own settings.** `docker-compose.yml` gives `backend-api` an explicit environment allowlist and no `env_file`, so a variable added only to `compose.hermes.yml` is invisible to the API. The `backend-api` service gains `HERMES_CHAT_ENABLED`, `HERMES_CHAT_MODEL`, `HERMES_CHAT_API_SERVER_KEY`, `HERMES_CHAT_BASE_URL`, `MLFLOW_HERMES_CHAT_EXPERIMENT`, and every `ADMIN_CHAT_*` setting that CHAT-002, CHAT-003, CHAT-004, CHAT-006, CHAT-007, CHAT-008, and CHAT-009 introduce (`ADMIN_CHAT_MAX_PROMPT_CHARS`, `ADMIN_CHAT_MAX_MESSAGES`, `ADMIN_CHAT_TURN_TIMEOUT_SECONDS`, `ADMIN_CHAT_DIGEST_MAX_CHARS`, `ADMIN_CHAT_MAX_TURNS_PER_WINDOW`, `ADMIN_CHAT_TURN_WINDOW_SECONDS`, `ADMIN_CHAT_MAX_CONCURRENT_TURNS`, `ADMIN_CHAT_ACTION_TTL_SECONDS`, `ADMIN_CHAT_STEP_TAIL`, `ADMIN_CHAT_STEP_READ_BYTES`, `ADMIN_CHAT_AUDIT_RETENTION_DAYS`). The same names are added to `Settings` in `src/deepfix_rag/config/settings.py`, with `HERMES_CHAT_ENABLED: bool = False` (D-1), `HERMES_CHAT_BASE_URL: str = "http://hermes:8643"`, and `ADMIN_CHAT_AUDIT_RETENTION_DAYS: int = 14` (D-6).
- [ ] **The `hermes` container can see what boot reads.** `chat_profile_config` reads `HERMES_CHAT_MODEL` and CHAT-011's boot reads `MLFLOW_HERMES_CHAT_EXPERIMENT`, both inside the `hermes` service, whose environment is also an explicit allowlist. Both names are added to the `hermes` service in `compose.hermes.yml`, and `HERMES_MODEL` stays there because it is the fallback D-3 reads.
- [ ] **Empty values must not crash boot or the API.** Typed settings reject an empty string: a `bool` field with `FLAG=''` raises `bool_parsing` and an `int` with `N=''` raises `int_parsing` under this repo's pydantic-settings (verified locally). So `HERMES_CHAT_ENABLED` and every `ADMIN_CHAT_*` integer use a **non-empty default in the Compose line** (`${HERMES_CHAT_ENABLED:-false}`, `${ADMIN_CHAT_MAX_PROMPT_CHARS:-48000}`, …), matching how `OPTIMIZATION_MIN_DEV_QUESTIONS: ${…:-50}` already does it. Only `str` fields may use `${VAR:-}`. A test composes the file with all chat variables unset and asserts `Settings()` builds.
- [ ] The plumbing test covers both services: every `ADMIN_CHAT_*`, `HERMES_CHAT_*`, and `MLFLOW_HERMES_CHAT_EXPERIMENT` name read by `Settings` or by `sandbox_boot.py` appears in `example.env`, in the `backend-api` environment, or in the `hermes` environment as appropriate, so a future setting cannot be added without being plumbed.
- [ ] `example.env` documents every new variable with a one-line comment. `HERMES_CHAT_API_SERVER_KEY` is already there (line 104) and needs no new line; `HERMES_CHAT_ENABLED=false`, `HERMES_CHAT_MODEL` (commented out, D-3), `HERMES_CHAT_BASE_URL` (commented, default), `MLFLOW_HERMES_CHAT_EXPERIMENT`, and the `ADMIN_CHAT_*` names are added. Together with the two criteria above, this is the plumb-through that `create-story` should implement as one change, not three.
- [ ] `HERMES_CHAT_ENABLED=false` on `Settings` makes every chat route return 503 through the router gate, except the audit and thread-delete routes that D-4 keeps available (CHAT-003), which is what makes the API side of D-1 testable without a container.
- [ ] `chat_profile_config` keeps the toolset list free of `terminal`, `file`, and `code_execution`, and the shipped `CHAT_API_TOOLSETS` stays `("skills", "web", "vision", "todo", "memory")`. A test asserts those three names are absent.
- [ ] The comments and copy that currently describe the chat profile as not started are updated in the same change, because leaving them is the kind of drift that makes the next reader doubt the code: `example.env:103` (“That profile is not started.”), `compose.hermes.yml:4`, `docker/hermes/Dockerfile:37`, and the chat `SOUL.md` text written by `sandbox_boot.py` (“the future admin chat”). Each now says the profile is a standalone gateway gated on `HERMES_CHAT_ENABLED`, not that it is idle. A test greps those four places for “not started” and “future admin chat” and fails on a match.
- [ ] `tests/test_hermes_sandbox.py` (or a new `tests/test_hermes_chat_profile.py`) asserts: both profiles are configured, the two bearer keys differ, the chat profile has no S3 or OpenRouter key, `gateway.standalone: true` is present on the chat profile and the optimization profile is unchanged, and the optimization profile's eval env file is not readable from the chat profile's config.
- [ ] `tests/test_hermes_optimization.py`-style assertions are unchanged: `agent.max_turns` stays 40 and `OPTIMIZATION_AGENT` stays `hermes` as the only value.
- [ ] With chat enabled, from the API container, `GET http://hermes:8643/health` returns `{"status": "ok"}`, and `POST http://hermes:8643/v1/chat/completions` with `{"model": "hermes-agent", "messages": [{"role": "user", "content": "say ok"}]}` and the chat bearer returns a `chat.completion`. A request to `POST http://hermes:8642/p/chat/v1/chat/completions` must **not** work, which is how the standalone setting is proven. Recorded in the verification note.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-002: Chat store and audit tables

**Description:** As a developer, I want one module that owns threads, turns, messages, action cards, and audit rows, so routes stay thin.

**Acceptance Criteria:**

- [ ] A new domain module `src/deepfix_rag/admin_chat.py` owns the schema and every read and write, using the existing `deepfix_rag.storage.connect` context manager and the same SQLite file as `optimization_runs` (`settings.product_db_path`). It exposes `init_admin_chat(db_path)` and is called from the API lifespan next to `init_optimization` in `src/deepfix_rag/api/app.py`.
- [ ] Tables, created with `CREATE TABLE IF NOT EXISTS`, are additive only (no change to `optimization_runs`):

| Table | Columns |
| :--- | :--- |
| `admin_chat_threads` | `thread_id` PK, `title`, `created_by`, `created_by_email`, `created_at`, `updated_at` |
| `admin_chat_turns` | `turn_id` PK, `thread_id`, `admin_id`, `admin_email`, `status` (`running`/`completed`/`failed`/`cancelled`), `retry_of`, `error`, `request_chars`, `first_token_ms`, `started_at`, `ended_at` |
| `admin_chat_messages` | `message_id` PK, `thread_id`, `turn_id`, `role` (`user`/`assistant`), `seq` INTEGER, `content`, `created_at` |
| `admin_chat_actions` | `action_id` PK, `thread_id`, `turn_id`, `kind`, `payload`, `status` (`proposed`/`confirmed`/`rejected`/`expired`/`failed`), `created_at`, `expires_at`, `decided_at`, `decided_by`, `result` |
| `admin_chat_thread_runs` | `thread_id`, `run_id`, `linked_at`; primary key `(thread_id, run_id)` |
| `admin_chat_audit` | `event_id` INTEGER PK AUTOINCREMENT, `at`, `admin_id`, `admin_email`, `thread_id`, `turn_id`, `kind`, `detail` |

`admin_chat_thread_runs.run_id` carries no foreign key to `optimization_runs`: a linked run is a pointer for display, and a pruned run row must not cascade-delete a thread.

- [ ] `kind` on `admin_chat_audit` accepts exactly these values, enforced by a `CHECK`: `turn_started`, `turn_completed`, `turn_cancelled`, `turn_failed`, `rate_limited`, `action_proposed`, `action_confirmed`, `action_rejected`, `action_expired`, `thread_deleted`.
- [ ] Indexes exist for the reads the routes make: `admin_chat_threads (created_by, updated_at DESC)`, `admin_chat_messages (thread_id, seq)`, `admin_chat_turns (thread_id, started_at)`, `admin_chat_turns (admin_id, started_at)` for the rate-limit count, and `admin_chat_actions (thread_id, status)`. Created with `CREATE INDEX IF NOT EXISTS` so an existing database picks them up.
- [ ] `create_thread(db_path, admin, title=None) -> Thread`, `list_threads(db_path, admin_id, limit=50)`, `get_thread(db_path, thread_id, admin_id)`, `delete_thread(db_path, thread_id, admin_id)`, `append_message(...)`, `update_assistant_text(...)`, `start_turn(...)`, `finish_turn(...)`, `thread_runs(...)`, `record_audit(...)`, `count_recent_turns(...)` exist as typed functions. A thread and its turns are visible only to the admin id that created them; a second admin gets `KeyError`, which the route maps to 404.
- [ ] `delete_thread` is a **hard delete** in one transaction and in the order D-4 fixes: write `thread_deleted` first (with the thread id and the deleted counts in `detail`), then delete the thread's action cards, then its messages, then its turns, then its `admin_chat_thread_runs` rows, then the thread row. Deleting a thread twice returns 204 both times and writes `thread_deleted` only on the first. `optimization_runs` rows and `admin_chat_audit` rows are never deleted.
- [ ] `admin_chat_audit` has **no foreign key** to `admin_chat_threads` and no `ON DELETE CASCADE`. A deleted thread leaves its audit rows with a dangling `thread_id`, deliberately. A schema test asserts no foreign key references `admin_chat_threads` from the audit table.
- [ ] Tests use `tmp_path` and the real SQLite file. No mocks. They cover: create/list/get/delete isolation between two admin ids; audit `kind` rejection of an unknown value; `expires_at` handling; ordering by `updated_at` descending; a delete removing turns, messages, and actions but leaving the audit rows and the `optimization_runs` row intact; and a second delete being a no-op with one `thread_deleted` row.
- [ ] Secret redaction covers the chat key. The redaction helper the module exposes is built on `optimization._secrets` / `optimization._redact`, and `HERMES_CHAT_API_SERVER_KEY` is added to the `_SECRET_ENVS` tuple in `src/deepfix_rag/optimization.py` (today it lists `API_SERVER_KEY`, `OPENROUTER_API_KEY`, `S3_SECRET_ACCESS_KEY`, `HERMES_S3_SECRET_ACCESS_KEY`, and `LLM_API_KEY` only). A test sets a chat key, passes it through the helper, and asserts `[redacted]` and no substring of the key.
- [ ] A thread title defaults to the first 60 characters of its first user message (whitespace collapsed), with no model call.
- [ ] `prune_audit(db_path, retention_days, *, now=None) -> int` deletes `admin_chat_audit` rows whose `at` is older than `retention_days` and returns the number deleted. `retention_days` is an argument with no default literal of its own; the setting is `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (default 14). A value of `0` or less deletes nothing and returns `0`, which is the documented way to keep rows forever (D-6). A test writes rows at 1, 13, 15, and 400 days old against a fixed `now` and asserts exactly the old ones go, that a second prune deletes nothing, and that `retention_days=0` deletes nothing.
- [ ] The prune is not itself audited and touches no other table. A test asserts `admin_chat_audit` has no row of a prune-specific kind and that `admin_chat_threads`, `admin_chat_messages`, and `optimization_runs` row counts are unchanged by a prune.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass, and `uv run --env-file .env pytest -m "not network and not e2e"` includes the new tests.

### CHAT-003: The streaming proxy route

**Description:** As an admin, I want my message to reach Hermes and its reply to stream back, without any key in my browser.

**Acceptance Criteria:**

- [ ] `POST /admin/chat/completions` (`operation_id` `admin_chat_completion`) in a new `src/deepfix_rag/api/routes/admin_chat.py`, registered in `app.py`. It calls `require_admin` from `src/deepfix_rag/api/deps.py`: 401 without a bearer, 403 for a member. The route handler does no domain work itself; it calls `admin_chat`.
- [ ] The request body is OpenAI-shaped and validated by a Pydantic model, not passed through: `thread_id: str`, `messages: list[{role: "user" | "assistant", content: str}]`, `retry_of: str | None`. The server builds the upstream `messages` from the thread's stored messages plus the last element of the request, which must be a `user` message. If the request's last element is not a `user` message, or its earlier elements disagree with what the thread already stored, the route returns 400 “The conversation is out of date. Reload the thread.” — the client cannot rewrite history or the prompt.
- [ ] Limits, all from settings, all with a default and a documented env name: total request characters `ADMIN_CHAT_MAX_PROMPT_CHARS` (default 48000), messages per request `ADMIN_CHAT_MAX_MESSAGES` (default 60), and wall-clock per turn `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` (default 300). Exceeding a size limit is 400 with a readable sentence, not a stack trace.
- [ ] The whole router is gated on `HERMES_CHAT_ENABLED` (D-1, D-2) **with two exceptions: `GET /admin/chat/audit` and `DELETE /admin/chat/threads/{thread_id}`.** When chat is off, every other `/admin/chat/*` route returns **503** with `code: "chat_disabled"` and the exact sentence “Hermes chat is turned off on this server.”, before any Hermes contact. The two exceptions exist because they serve what chat already wrote: disabling a feature must not hide its audit trail (D-4) and must not strand conversation text an admin can no longer delete. A test asserts the disabled 503 for the other nine routes, 200 (not 503) for audit, 204 for delete, and that the disabled and misconfigured bodies differ.
- [ ] **Error codes need a mechanism, because the shared model has none.** `src/deepfix_rag/api/schemas.py`'s `ErrorResponse` is `detail: str` only, and `web/src/services/api-result.ts` reads only a string `detail`. The chat routes therefore return their errors as `HTTPException(status_code=503, detail={"code": "chat_disabled", "message": "..."})`, which FastAPI serialises as a nested `detail` object, and `ErrorResponse` gains an optional alternative shape — `detail: str | ChatErrorDetail` where `ChatErrorDetail` is `{code: str, message: str}` — so the OpenAPI stays honest without changing any other route's `detail: str`. `api-result.ts`'s `errorDetail` gains a branch that reads `detail.message` when `detail` is an object, and `admin-chat-service.ts` exposes the `code`. A test asserts the OpenAPI for the chat routes documents the object form, every existing route's schema is unchanged, and `errorDetail` returns the message for both shapes.
- [ ] The two codes are exactly `chat_disabled` and `chat_misconfigured`; the misconfigured body names the missing variables in `message`. The UI branches on `code`, never on the sentence (FR-028).
- [ ] **Order of checks is specified per route class.** Identity first, then admin role, on every route — so an unsigned caller still gets 401 and a member 403 even when chat is off, and the disabled 503 can never be an unauthenticated probe. After that:
  - **Hermes-facing routes** (completions, turn stream, thread CRUD/read, turn create, action confirm/reject, run link/read): `HERMES_CHAT_ENABLED`, then configuration, then work.
  - **The two non-Hermes routes** (`GET /admin/chat/audit`, `DELETE /admin/chat/threads/{thread_id}`): **no enabled check and no configuration check.** They read and write SQLite only. A missing chat key must not stop an admin from deleting their conversation or reading the audit trail, for the same reason D-4 gives about turning the feature off.
  `current_user` can also raise 503 when the identity provider is unreachable; that body carries the identity detail and no `code`, so it is distinguishable from both chat 503s. A test covers an unsigned caller and a member against a disabled deployment, and asserts the audit and delete routes still work with chat disabled **and** with the chat key unset.
- [ ] The upstream call is `POST http://hermes:8643/v1/chat/completions` with `Authorization: Bearer <HERMES_CHAT_API_SERVER_KEY>`, `Content-Type: application/json`, `stream: true`, `model` from `HERMES_CHAT_MODEL` falling back to `HERMES_MODEL` (D-3), and `messages` = server system message + the thread's stored history + the new user message. The base URL comes from `HERMES_CHAT_BASE_URL` (default `http://hermes:8643`). A test asserts the upstream body carries the fallback model when `HERMES_CHAT_MODEL` is unset.
- [ ] **No `X-Hermes-Session-Id`.** The chat surface is stateless and the proxy is the only source of history (FR-005, FR-007), so a server-side Hermes session would be a second copy of the conversation that could drift from the stored thread and double it in the model's context. Each turn sends the whole history. The `admin_chat_turns` table has no `hermes_session_id` column for that reason; if a future story adds one, it must also stop sending the history, not both.
- [ ] `require_model`-style behaviour for the enabled-but-incomplete case: if `HERMES_CHAT_API_SERVER_KEY` is empty, or both `HERMES_CHAT_MODEL` and `HERMES_MODEL` are empty, the route returns 503 with `code: "chat_misconfigured"` and the sentence “Admin chat is not configured on this server: set HERMES_CHAT_API_SERVER_KEY and HERMES_CHAT_MODEL or HERMES_MODEL.” and does not call Hermes. No model id is hardcoded.
- [ ] The proxy is transparent except for one field: it parses each `data:` JSON frame, deletes any `model` key from the object, and re-serialises that frame, so the browser never learns the upstream model id (FR-003). Every other key and value is preserved, `: keepalive` comments are forwarded untouched, and a `data:` payload that is not a JSON object is **dropped and logged rather than forwarded**, because an opaque payload could carry the model id the rule exists to hide. A test asserts the forwarded stream carries no model id, that a non-JSON frame never reaches the client, and that the assistant text, `finish_reason`, and tool-progress event all still do.
- [ ] The reply is relayed as SSE, in order, with `media_type="text/event-stream"`, `Cache-Control: no-cache`, `Connection: keep-alive`, and `X-Accel-Buffering: no`. Hermes `: keepalive` comments are forwarded. The route does not buffer the whole reply: the test server delays its second chunk, and the test asserts the first frame reached the client before that delay ended.
- [ ] `Content-Type: application/json` (non-streaming) is refused with 400. The chat UI always streams.
- [ ] On completion the route stores the assistant text, sets `first_token_ms` and `ended_at`, marks the turn `completed`, appends `turn_completed`, and emits one final SSE frame with a JSON object `{"type": "deepfix.turn", "turn_id", "status", "actions": [...]}` before closing.
- [ ] `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` is enforced as a wall-clock deadline on the relay. On expiry the route emits `{"type": "deepfix.error", "message": "This reply took too long and was stopped."}`, marks the turn `failed` with that reason, closes the upstream request, and writes `turn_failed`. The partial text stays stored, exactly as for a cancel.
- [ ] On an upstream error before any token, the route returns 502 with a readable sentence. After the first token it emits `{"type": "deepfix.error", "message": ...}` and closes. Neither path includes the upstream body verbatim; both pass through the secret redaction helper (`optimization._redact` today) so a key echoed by a misconfigured server cannot reach the browser. A test asserts `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, and `OPENROUTER_API_KEY` do not appear in any error frame or audit `detail`.
- [ ] The turn is recorded in the audit log as `turn_started` before the upstream call and `turn_completed` / `turn_failed` after it, with the admin id and email, the thread id, and the turn id.
- [ ] Tests run against a real local HTTP server that stands in for the chat profile (no `unittest.mock`), covering: 401, 403, a streamed reply split across several chunks, a `: keepalive` forward, upstream 500 before the first token, an error after the first token, an oversized prompt giving 400, a `system` role giving 400, a request whose history disagrees with the stored thread giving 400, and the upstream body containing exactly the stored thread history plus the new user message.
- [ ] Two more tests for the new states: `HERMES_CHAT_ENABLED=false` gives 503 with the disabled sentence for the nine Hermes-facing routes and touches no upstream server (the test server receives zero requests), while `GET /admin/chat/audit` answers 200 and `DELETE /admin/chat/threads/{id}` answers 204; `HERMES_CHAT_ENABLED=true` with an empty chat key gives 503 with `code: "chat_misconfigured"` on the Hermes-facing routes, while the audit and delete routes still work. Both assert the two sentences and codes differ, so the UI can tell “off” from “broken”.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass, and the OpenAPI document regenerates (`uv run python -m deepfix_rag.api.export_openapi`) with the new operation id.

### CHAT-004: Server prompt and status digest

**Description:** As an admin, I want the assistant to know the current schedule, champion, and recent runs, so its first answer is about my server rather than about itself.

**Acceptance Criteria:**

- [ ] `admin_chat.system_prompt(digest, thread_id, turn_id)` returns a fixed policy text plus a per-turn digest, and embeds the exact sentence CHAT-011 depends on: `The DeepFix chat thread id is <thread_id> and the turn id is <turn_id>.` The ids are explicit arguments, not derived from the digest. The policy text says: this is the DeepFix admin chat; the assistant explains and proposes, it does not promote; it must not claim to have promoted anything; it must not ask for or repeat credentials; it names the gate thresholds when it talks about promotion; it may propose an action card with the exact block described in CHAT-007.
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
- [ ] `POST /admin/chat/threads` (`create_admin_chat_thread`) creates a thread, and `DELETE /admin/chat/threads/{thread_id}` (`delete_admin_chat_thread`) is a hard delete in the children-before-parent order D-4 fixes — action cards, messages, turns, then the thread's `admin_chat_thread_runs` rows, then the thread row — audited as `thread_deleted`, and idempotent: 204 both times. Audit rows and `optimization_runs` rows are untouched. Delete stays available when chat is disabled (D-4, CHAT-003).
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
- [ ] `GET /admin/chat/audit?limit=50` (`list_admin_chat_audit`) returns the newest audit rows, including `admin_email`, `thread_id`, `turn_id`, `kind`, `detail`, and `thread_deleted`. Admin only. A member receives 403.
- [ ] `thread_deleted` is `true` for a row whose `thread_id` no longer exists in `admin_chat_threads` (D-4). The flag is computed at read time with one `LEFT JOIN`, not stored, so it cannot go stale. Rows for a deleted thread are still returned, and their `turn_id` is not resolvable — the flag already says so. A test deletes a thread and asserts its audit rows come back with `thread_deleted: true`, their `kind` and `detail` intact, and no message text anywhere in the response.
- [ ] A `rate_limited` row is written by the limiter itself and never causes a second limiter pass: the count used by the limiter is `count_recent_turns`, which reads `admin_chat_turns`, so audit writes cannot inflate it and a refusal cannot ratchet itself. The “one per minute per admin” rule is a `SELECT` on the newest `rate_limited` row for that admin.
- [ ] Every audit `detail` is redacted through the same helper as the step log and truncated to 500 characters, matching `STEP_PREVIEW_LIMIT`.
- [ ] **The retention prune is wired into the existing scheduler tick (D-6, FR-030).** `OptimizationScheduler` gains an optional `prune_audit` hook (a callable taking no arguments, injected by `app.py` as `lambda: admin_chat.prune_audit(db_path, settings.ADMIN_CHAT_AUDIT_RETENTION_DAYS)`), invoked from `_loop` at most once per hour and **before** the due-run check so a slow prune never delays a run.
- [ ] **The loop must be able to wake hourly.** Today `_loop` waits until the next scheduled run, which with the default 02:00 cron is up to 24 hours, so a prune hung off the tick alone would run about once a day at an unpredictable hour. The wait becomes `_idle_delay(seconds_until_next_run: float | None) -> float`, returning `PRUNE_MAX_IDLE_SECONDS = 3600` when the schedule is paused (`None`) and `min(seconds_until_next_run, 3600)` otherwise, so the thread wakes at least hourly and `run_due()` returns immediately when nothing is due. A unit test asserts 3600 for a run 24 hours away, the real gap for a run 90 seconds away, and 3600 (not 0 or negative) when the schedule is paused, so a paused schedule still prunes.
- [ ] The hourly guard on the prune itself is an in-memory last-run timestamp on the scheduler instance; a restart therefore prunes on its first tick, which is the desired behaviour after a settings change.
- [ ] The wiring is **not** gated on `HERMES_CHAT_ENABLED`: the prune runs from the scheduler loop, which knows nothing about chat. A test constructs the scheduler with `HERMES_CHAT_ENABLED=False`, inserts an audit row past the window, drives one tick, and asserts the row is gone. Another drives two ticks inside the hour and asserts the injected callable ran once (the counter is the callable's own, so the test needs no clock).
- [ ] The prune reads `ADMIN_CHAT_AUDIT_RETENTION_DAYS` on each invocation, not at construction, so a changed setting takes effect on the next tick. A test sets the value to `0` and asserts nothing is deleted.
- [ ] The scheduler logs one line per prune that deleted something — `chat audit prune deleted=<n> retention_days=<d>` — and nothing when the count is zero (NFR-8). A failure inside the prune is caught and logged, never fatal to the scheduler thread: the existing `except Exception: LOGGER.exception("Scheduled optimization failed")` pattern is the model, and a test injects a raising callable and asserts the tick returns normally.
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
- [ ] A card may be confirmed **while the thread's turn is still `running`**. The conversation plane and the job plane are separate (see “What the two planes are”), and `start_optimization_run` already refuses a second concurrent run, so blocking the button until a long reply ends would add no safety and would delay a deliberate admin action. A test confirms a card during an open stream and asserts the run is created and the stream is unaffected.
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

- [ ] The generated client is regenerated from `web/openapi.json` with `npm run generate:api` (`@hey-api/openapi-ts` 0.99.0). No hand-written `fetch` for a JSON route. The streaming route needs its own reader because the generated client does not stream, and that reader lives in **`web/src/services/admin-chat-sse.ts`**.
- [ ] `web/src/test/api-boundary.test.ts` currently fails any `fetch(` outside the generated client, `server.ts`, and `api.ts`. `admin-chat-sse.ts` is added to that file's `EXEMPT` set **as its relative path, `join("services", "admin-chat-sse.ts")`** — the test matches with `EXEMPT.has(relative(SRC, file))`, so a bare filename would silently miss and the test would fail. Its own test asserts the only request target in that module is a `/api/admin/chat/...` path, so the exemption is narrow rather than a hole. FR-026's "unchanged except" list names this file.
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
- [ ] **Disabled state (D-2):** the flag reaches the page without a chat call. `GET /admin/optimization` gains one field, `chat_enabled: bool`, read from `settings.HERMES_CHAT_ENABLED` in the existing `_status` handler; it is not a new route, so the eleven-route count in NFR-4 is unchanged. The `/admin/chat` loader reads it from the same `optimizationQueryOptions()` data the Optimization screen already fetches, and renders `<AdminChatDisabled />` when it is false. In that state the page makes **zero** requests to any `/admin/chat/*` route — no thread list, no resume, no polling. A test renders the page with `chat_enabled: false` and a fetch spy and asserts zero chat calls (the optimization query itself is allowed).
- [ ] **Adding `chat_enabled` breaks an existing web fixture, and that fix is part of this story.** `ApiModel` sets `json_schema_serialization_defaults_required=True`, so a new `chat_enabled: bool` becomes a **required** field of `OptimizationStatusResponse` in the generated client, and `web/src/test/optimization-page.test.tsx`'s `status()` fixture (typed as `OptimizationStatus`) stops compiling. The fixture gains `chat_enabled: true` (and one case with `false`), and the same field is added wherever else an `OptimizationStatus` literal is built. A test asserts the Optimization screen renders unchanged with `chat_enabled: true`.
- [ ] **Misconfigured state:** a 503 whose body is the D-3 misconfiguration sentence renders a different, diagnostic message naming the missing variables, so an operator can tell “turned off” from “turned on but broken”.
- [ ] The chat is built with `@assistant-ui/react` `^0.15.28` (already in `web/package.json`) using `AssistantRuntimeProvider` with `useExternalStoreRuntime`, the same pattern `web/src/features/chat/chat-page.tsx` already uses, and `ThreadPrimitive`, `MessagePrimitive`, `ComposerPrimitive`, `ThreadListPrimitive`, and `ActionBarPrimitive`. `onNew` sends a turn, `onReload` sends a retry, `onCancel` aborts and records the cancel. Each of those three handlers exists; the story's verification note records that the installed 0.15.28 supports them.
- [ ] **Streaming:** assistant text appends as frames arrive, with no reload of the thread. The viewport follows the stream and stops following when the admin scrolls up.
- [ ] **History:** a thread list on the left, newest first, with a **New chat** control and a delete control per thread. Selecting a thread loads its messages. A reload reopens the last thread from the URL (`?thread=<id>`) or the newest thread. The delete control opens a confirmation dialog that names what will be removed — the conversation, its messages, and any pending action cards — and states that the record of any run started from it (who started it, not what was said) is kept (D-4). Confirm calls `deleteThread` and removes the row; the dialog is the only place a thread is deleted, there is no optimistic removal before the server answers.
- [ ] **Markdown and code:** assistant text renders with `react-markdown` and `remark-gfm` (already dependencies). Fenced code renders in a bordered `pre > code` block with a copy button and no syntax highlighter. User messages render as plain text, never as HTML.
- [ ] **Tool and progress:** a `hermes.tool.progress` event renders as a collapsed step row (“Searching the web…”, then its result preview). A linked optimization run renders a run panel: status, elapsed, reason, dev delta, aggregate holdout delta, wins/losses, MLflow link, and the step tail, polled every 5 seconds while the run is `running` and once on mount.
- [ ] **Stop and retry:** while a turn runs the composer shows **Stop**; pressing it aborts, keeps the partial text, and shows “Stopped”. Each assistant message has a retry control that resends its user message.
- [ ] **Actions:** an action card shows what it will do in one sentence (`Start a run now` / `Cancel run <id>`), the reason the assistant gave, and **Confirm** and **Dismiss**. Confirm shows the resulting run id and status. A 409 or 503 from confirm renders inline on the card, and the card stays confirmable when the failure is retryable.
- [ ] **Run header:** the page header shows whether a run is in progress, using `optimizationQueryOptions()`; a running job disables nothing in the chat, because chat and job are separate.
- [ ] **Reconnect:** if a turn was running when the tab closed, reopening the thread calls `resumeTurn` and appends the tail; if the run is still `running`, the run panel resumes polling. A test drives `resumeTurn` with a stored partial message and asserts the text appears once, not twice.
- [ ] **Accessibility:** the composer has a labelled textarea, the streamed region is `aria-live="polite"`, the stop and confirm controls are real buttons with accessible names, focus returns to the composer after a turn, and the thread list is a labelled list. `web/src/test/admin-chat-page.test.tsx` asserts the composer label, the stop button, the denied page for a member, an action card's accessible name, the “no threads yet” empty state, the disabled state with zero fetch calls, the misconfigured message, and the delete confirmation dialog's accessible name and its two buttons.
- [ ] `bun run typecheck`, `bun run lint`, and `bun run test` pass. The existing Ask screen tests (`web/src/test/pages.test.tsx`, `chat-page`) still pass unchanged.
- [ ] Verify in browser: an admin sees a streamed reply, a step row, a stop that leaves partial text, a retry, an action card that starts a run, and a delete confirmation; a member opening `/admin/chat` sees the denied page and no composer; with `HERMES_CHAT_ENABLED=false` the page shows the disabled state and the network panel shows no chat request. Recorded in the verification note with the commands used.

### CHAT-011: Chat tracing in MLflow

**Description:** As the operator, I want each chat turn to be a trace, so a bad answer can be inspected without reading container logs.

**Acceptance Criteria:**

- [ ] Boot installs `hermes-otel` and `deepfix-run-tag` on the chat profile too, with its own `hermes_otel.yaml` inside `profiles/chat/`, `content_capture: "off"`, `capture_conversation_history: false`, `capture_sender_id: false`, and the MLflow backend aimed at `MLFLOW_TRACKING_URI`.
- [ ] The experiment name comes from `MLFLOW_HERMES_CHAT_EXPERIMENT` (default `deepfix_hermes_chat`) and is created at boot when MLflow answers. This is Decision D-5: a **separate** experiment, not a `deepfix.surface` attribute on the optimizer's. The optimization profile keeps `deepfix_hermes`, and a test asserts the two experiment names differ. `ensure_hermes_trace_experiment(tracking_uri)` currently hardcodes the optimizer's experiment name, so it gains a `name` parameter that the chat call passes; the existing optimizer call keeps its default and its test stays green.
- [ ] `deepfix-run-tag` learns a second label. The server prompt contains the exact sentence `The DeepFix chat thread id is <thread_id> and the turn id is <turn_id>.` The plugin sets `deepfix.chat_thread_id` and `deepfix.chat_turn_id` on spans, and keeps `deepfix.run_id` working for optimization rounds. Its secret scrubbing is unchanged.
- [ ] Tests: a scripted turn run through the plugin hooks (as `tasks/prd-hermes-optimizer.md` US-007 did) exports a trace whose spans carry both chat attributes and no `API_SERVER_KEY` or `HERMES_CHAT_API_SERVER_KEY`. Existing `tests/test_hermes_run_tag.py` cases still pass.
- [ ] The trace does not carry the prompt text or the reply text, because content capture stays off. The API's own SQLite store is the transcript of record.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### CHAT-012: Deployed verification on Dokploy

**Description:** As an operator, I want one end-to-end pass on the deployed stack before this is called done.

**Acceptance Criteria:**

- [ ] Executed by the operator on the Dokploy deployment, not by a cloud agent, and it does not push to `dev` or `main`.
- [ ] Both profiles behave as decided: with `HERMES_CHAT_ENABLED=true`, `docker port hermes` is empty, `curl` from the host to 8642 and 8643 is refused, `GET http://hermes:8643/health` succeeds from `backend-api`, and `POST http://hermes:8642/p/chat/v1/chat/completions` **fails** (the standalone check). With `HERMES_CHAT_ENABLED=false`, nothing listens on 8643, the nine Hermes-facing chat routes return the disabled 503, `GET /admin/chat/audit` still answers 200, and `DELETE /admin/chat/threads/{id}` still answers 204.
- [ ] **Memory, both states (D-1, FR-029):** with chat off, `ps -eo pid,rss,args` inside the container lists exactly one `hermes gateway` process and `ss -ltn` shows nothing on 8643; with chat on, a second gateway process exists and 8643 is bound. `docker stats --no-stream` is recorded in both states for information. If the 4 GB limit is approached with chat on and a run in flight, the report names which cap to lower and **no cap is raised**.
- [ ] An admin completes J1, J2, J3, and J4 on the deployed site: a streamed answer, an action card that starts a run, the run panel moving through `running` to a terminal status, a stopped turn that keeps its partial text, a retry, a reload that resumes, and a thread delete whose audit rows survive with `thread_deleted: true`.
- [ ] With `HERMES_CHAT_ENABLED=false`, the deployed site shows the disabled page, makes no chat request, and the Optimization screen still starts and cancels a run.

## Functional Requirements

Priority is **Must** (the release is incomplete without it), **Should** (planned, may slip one release), or **Could** (nice, unplanned).

| ID | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-001 | `HERMES_CHAT_ENABLED` must control whether chat runs, and its default must be `false`. When true, the `chat` profile must run as a **standalone** gateway (`gateway.standalone: true`) on port 8643 with its own bearer key from `HERMES_CHAT_API_SERVER_KEY`, unreachable from outside the Compose network and not served under `/p/chat/` by the optimization gateway. When false, its gateway must not start, nothing may listen on 8643, and no chat gateway process may exist. | Must | D-1. Opt-in, and off must cost no memory. Multiplexing would otherwise serve chat from the optimization process. |
| FR-002 | The chat profile must have no `terminal`, `file`, or `code_execution` toolset, and its environment must contain no `OPENROUTER_API_KEY`, no `S3_*`, no `LLM_API_KEY`, and not the optimization `API_SERVER_KEY`. | Must | G3. A shell on the chat profile would read the optimization profile's `.env` on the shared volume. |
| FR-003 | The browser must reach the chat only through the existing same-origin `/api` proxy on the web server, and must never receive a Hermes key, an OpenRouter key, an S3 secret, or an upstream model id from the server. Because `chat.completion.chunk` frames carry a `model` field, the relay must delete that field from every frame before forwarding, preserving everything else. | Must | G2. Stated constraint. FR-007 forwards content faithfully, so removing `model` is the one deliberate exception and must be explicit. |
| FR-004 | deepfix-api must expose `POST /admin/chat/completions`, admin-only, that accepts an OpenAI-shaped `messages` array and streams an SSE reply. A member must receive 403 and an unsigned caller 401. | Must | G1, G2. The single entry point the architecture names. |
| FR-005 | The request role enum must be `user` and `assistant` only, so a `system` or `tool` role is refused; the system prompt and the status digest must be built server-side on every turn, and the upstream conversation must come from the stored thread, not from the client's array. | Must | G3. Otherwise the browser can rewrite the policy that forbids promotion, or claim a history that never happened. |
| FR-006 | The status digest must be built from `optimization_schedule`, `champion_diff`, and `recent_runs`, must name the gate thresholds, and must contain no holdout question, holdout id, document text, private path, or credential. Agent-written fields (`reason`, `summary`) must be wrapped in explicit delimiters and labelled as untrusted data, so a run reason cannot act as an instruction. | Must | G1, G3. The answers must be about this server, must not leak, and must not let stored agent text steer the chat. |
| FR-007 | The proxy must forward to `HERMES_CHAT_BASE_URL` (default `http://hermes:8643`) with the chat bearer, `stream: true`, the server system message, and the conversation read from the thread's stored messages, ending with the request's final user message; it must relay SSE frames in order, including `: keepalive` comments, without buffering the whole reply, and must strip the `model` field from each JSON frame as FR-003 requires. | Must | G4. A buffered relay would look dead for minutes. |
| FR-008 | A browser abort must mark the turn `cancelled`, keep the partial assistant text, close the upstream request, and must not cancel an optimization run. | Must | G4. Stop means stop the reply; the job has its own cancel. |
| FR-009 | A retry must create a new turn linked by `retry_of` and stream a fresh reply. | Must | G4. A failed turn must be recoverable without retyping. |
| FR-010 | Threads, turns, messages, and actions must persist in the API's SQLite database, scoped to the admin who created them, and survive an API restart. Deleting a thread must hard-delete its action cards, messages, turns, thread-run links, and then the thread row, children before parent, in one transaction. | Must | G4. Reload and reconnect depend on it; D-4 fixes what delete means. |
| FR-011 | `GET /admin/chat/turns/{turn_id}/stream` must replay the stored partial text and then tail a live turn, or return the stored terminal status after a restart. | Must | G4. Reload must not lose a reply in progress. |
| FR-012 | A turn left `running` by a restart must be marked `failed` with the interruption reason at API startup. | Must | G4. No row may stay `running` forever; mirrors `recover_running_runs`. |
| FR-013 | A completed reply must be scanned for `deepfix-action` blocks, and only `start_optimization_run` and `cancel_optimization_run` are recognised; an unknown kind is dropped and counted, not surfaced as an error. | Must | G3. The allowlist is the whole safety property of action cards. |
| FR-014 | `POST /admin/chat/actions/{action_id}/confirm` must be admin-only, single-use, owner-scoped, and expire after `ADMIN_CHAT_ACTION_TTL_SECONDS`; it must call the same domain functions the Optimization screen calls and return `OptimizationRunResponse`. | Must | G3. The admin's explicit confirmation is the only write path. |
| FR-015 | No chat code path may call `apply_candidate`, write `configs/champion.yaml` or `indexes/current`, or read holdout. | Must | G3. Stated constraint; the gate is not negotiable. |
| FR-016 | The run-to-thread link must persist in a `admin_chat_thread_runs` join table, and `GET /admin/chat/threads/{thread_id}/runs` must return each linked run's status, reason, deltas, MLflow link, and a redacted step tail capped at `ADMIN_CHAT_STEP_TAIL` lines, and must call `fail_stale_runs`. | Must | G1, G4. Progress display is what makes a multi-hour run usable, and it must survive a reload. |
| FR-017 | Rate limits must be enforced from persisted turns: per admin per window, one running turn per admin, and a global concurrent cap; a breach is 429 with `Retry-After`. | Must | G5. The container is 4 GB shared by two gateways. |
| FR-018 | Every chat-initiated action must write an audit row from the closed `kind` list, with the admin id and email, thread id, turn id, and a redacted 500-character detail; `GET /admin/chat/audit` must return them to admins only. Audit rows must never be deleted by a thread delete — only by age, through the FR-030 prune — must contain no message text, and must report `thread_deleted` for a gone thread. | Must | G6, D-4, D-6. The security record of who started a run must outlive the conversation, and must not outlive its retention window. |
| FR-019 | Per-turn wall clock must be bounded by `ADMIN_CHAT_TURN_TIMEOUT_SECONDS`, and request size by `ADMIN_CHAT_MAX_PROMPT_CHARS` and `ADMIN_CHAT_MAX_MESSAGES`; exceeding a bound is a readable 400 or a terminal error frame. | Must | G5. |
| FR-020 | The chat model must come from `HERMES_CHAT_MODEL` and fall back to `HERMES_MODEL` when unset or blank, read from the environment; when both are empty while chat is enabled the route must return a 503 naming both variables, and no model literal may exist in Python or TypeScript. `HERMES_CHAT_MODEL` ships commented out in `example.env`. | Must | D-3, and the repo's existing `require_model` rule. |
| FR-021 | Every error frame, audit `detail`, step tail, digest, and SSE error must pass through the existing secret redaction, extended to include `HERMES_CHAT_API_SERVER_KEY`; no response may contain `API_SERVER_KEY`, `HERMES_CHAT_API_SERVER_KEY`, `OPENROUTER_API_KEY`, an S3 secret, or a path under `PRODUCT_DATA_DIR`. | Must | G2, G6. The current `_SECRET_ENVS` tuple in `optimization.py` does not list the chat key, so extending it is part of the work, not an assumption. |
| FR-022 | The chat page must stream, show history, render markdown and code, show tool and job progress, stop, retry, and render confirmable action cards. | Must | G1, G4. The named UI features. |
| FR-023 | `/admin/chat` must be in the Admin navigation group and must render a denied page for a member, with no composer and no calls to the chat routes. | Must | G3. The Admin section boundary. |
| FR-024 | A chat turn must produce an MLflow trace in `MLFLOW_HERMES_CHAT_EXPERIMENT` (default `deepfix_hermes_chat`, separate from the optimizer's `deepfix_hermes`), tagged `deepfix.chat_thread_id` and `deepfix.chat_turn_id`, with content capture off. | Must | G6, D-5. Traceability without storing conversation text in MLflow; D-5 settles the place, so this is no longer deferrable. |
| FR-025 | The streamed reply region must be `aria-live="polite"`, the composer labelled, the stop and confirm controls named buttons, focus must return to the composer after a turn, and the disabled and misconfigured pages must use the same heading level and labelled-region pattern as the rest of the Admin section. | Must | G1 promises a keyboard- and screen-reader-usable page, and this is the only FR that delivers it. |
| FR-026 | The existing Ask screen, Optimization screen, and their tests must be unchanged by this feature, except for: the sidebar active-state fix, the `chat_enabled` field on the optimization status payload, the `admin-chat-sse.ts` entry in `api-boundary.test.ts`'s `EXEMPT` set, and the additions named here. | Must | No regression to shipped member and admin surfaces. |
| FR-027 | When `HERMES_CHAT_ENABLED` is false, `/admin/chat` must render an explicit disabled state that names `HERMES_CHAT_ENABLED=true` as the switch and must make zero requests to any `/admin/chat/*` route. The flag must reach the page through the existing `GET /admin/optimization` payload as `chat_enabled`, not through a chat or new route, and the sidebar entry must remain visible so the state is discoverable. | Must | D-2. A silent or hidden page looks like a broken deploy; a status route would break the locked route list. |
| FR-028 | A chat 503 must distinguish “turned off” from “turned on but misconfigured”: each carries a stable machine-readable `code` (`chat_disabled` and `chat_misconfigured`) alongside its sentence, the disabled body is the D-2 sentence, the misconfigured body names the missing variables, and the UI renders a different message for each. The client switches on `code`, never on the sentence. | Must | D-1, D-3. Two different operator actions must not look identical, and string matching would break on an edit. |
| FR-029 | With chat disabled, the container must have **exactly one `hermes gateway` process and nothing listening on 8643**. “Costs no memory” must be demonstrated by that process-and-port check, recorded from the deployment, with container RSS reported on and off for information only. | Must | D-1. Summed RSS cannot prove the claim; process and port absence can. |
| FR-030 | `admin_chat_audit` rows older than `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (default 14, `0` or negative disables the prune) must be deleted, by a prune that runs from the existing optimization scheduler tick at most once per hour and keeps running when `HERMES_CHAT_ENABLED=false`. No literal retention window may exist in Python. | Must | D-6, G6, NFR-5. Keep the audit trail without letting it grow forever, on deployments that have chat off as much as on. |

## Non-Functional Requirements

- **NFR-1 Reliability.** A chat turn must never leave `admin_chat_turns` in `running` after the API has restarted, and an optimization row must never be left `running` by a chat path (the existing `fail_stale_runs` and `recover_running_runs` rules continue to apply). A dropped browser connection is a normal end, not an exception in the API log.
- **NFR-2 Resource limits.** One optimization job at a time remains enforced by `start_optimization_run` raising `RunInProgress`. Chat adds at most `ADMIN_CHAT_MAX_CONCURRENT_TURNS` (default 2) concurrent turns and one `running` turn per admin. **With `HERMES_CHAT_ENABLED=false` the chat costs no resident memory: no chat gateway process and no listener.** No story raises `mem_limit: 4g`, `cpus: "2"`, `agent.max_turns: 40`, or `OPTIMIZATION_RUN_TIMEOUT_HOURS`.
- **NFR-3 Latency.** Time to first token is a property of Hermes and OpenRouter, not of this proxy; the proxy must not add buffering, so the first byte the browser sees is the first byte Hermes sent. A thread load must be two indexed reads, not a scan: a test asserts `EXPLAIN QUERY PLAN` for the messages-by-thread and threads-by-admin queries reports an index search, and that an index exists on `admin_chat_messages (thread_id, seq)` and `admin_chat_threads (created_by, updated_at)`. The numeric target of 300 ms on the pilot data (tens of threads, hundreds of messages) is a measurement recorded in CHAT-012, not a test assertion, because a wall-clock assertion on a shared CI machine is flaky.
- **NFR-4 Security.** Every `/admin/chat/*` route requires admin, and there are exactly eleven of them: `POST /admin/chat/completions`, `GET /admin/chat/turns/{turn_id}/stream`, `GET /admin/chat/threads`, `POST /admin/chat/threads`, `GET /admin/chat/threads/{thread_id}`, `DELETE /admin/chat/threads/{thread_id}`, `GET /admin/chat/threads/{thread_id}/runs`, `POST /admin/chat/threads/{thread_id}/runs/{run_id}`, `POST /admin/chat/actions/{action_id}/confirm`, `POST /admin/chat/actions/{action_id}/reject`, and `GET /admin/chat/audit`. Nine of them are Hermes-facing and gated on `HERMES_CHAT_ENABLED` and configuration; two (`GET /admin/chat/audit`, `DELETE /admin/chat/threads/{thread_id}`) are SQLite-only and stay reachable when chat is disabled, so an admin can still read the audit trail and delete their own conversation (D-4). A test walks `app.routes` and asserts every path starting `/admin/chat` is registered with the admin dependency, that none is unauthenticated, and that the exempt pair is exactly the two named here. The bearer key is sent only on the `Authorization` header to Hermes. The browser's better-auth cookie never leaves the web server (`src/api-proxy.ts` already strips `cookie` and `authorization`). No route accepts a URL that could be used to redirect the upstream call (the base URL is configuration, never request data). The chat profile keeps no terminal, so the `deepfix` shim, its secret scrub, and the read-only skill tree stay optimization-only.
- **NFR-5 Privacy.** Conversation content is stored in the API's SQLite store, which already holds feedback text and run reasons; it is not copied to S3, not returned to members, and not captured in MLflow. Holdout content is never in scope of any chat read. Audit rows hold no message text, only counts, statuses, ids, and the redacted detail, and they are pruned after `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (D-6), so the trail is bounded in time as well as in content.
- **NFR-6 Accessibility.** Keyboard reachable composer, stop, retry, confirm, dismiss, and thread list; visible focus; `aria-live` for streaming text; error text is announced via `role="alert"` as the existing pages do.
- **NFR-7 Compatibility.** `@assistant-ui/react` stays at `^0.15.28`; no new chat runtime package is added. The web app stays on the existing proxy and generated client; no new origin, no new server.
- **NFR-8 Operability.** The chat failure modes are visible in the API log with the same structured style as `optimization run=… event=…`: one line per turn start, per terminal state, and per refused action. The audit table is the durable record. The retention prune writes one line per run with the deleted count, and only when it deleted something, so an idle day adds no log noise.
- **NFR-9 Compliance.** N/A for a new regulatory obligation: the pilot handles no new personal data category (an admin's email is already stored on `optimization_runs` and in Better Auth), no payment data, and no special-category data. The applicable existing obligation is data minimisation, which NFR-5 satisfies by keeping conversation text out of MLflow and out of the audit table. Revisit if a member-facing chat is ever proposed.

## Acceptance criteria (release level)

1. An admin on a deployment with `HERMES_CHAT_ENABLED=true` opens `/admin/chat`, sends a message, and reads a streamed, markdown-rendered reply whose content reflects the current schedule, champion, and recent runs.
2. The reply can propose an action; only after the admin presses Confirm does a run start, and the audit log and the run row both show it.
3. The run's status, reason, deltas, MLflow link, and last step lines appear in the thread and survive a page reload; a reload during a reply resumes the reply.
4. Stop leaves the partial reply visible, stores it, and does not cancel the run.
5. Retry produces a new turn and a new reply.
6. A member can reach neither the page nor any chat route, and no member-facing screen changed.
7. No response, audit row, log line, or trace contains a Hermes key, an OpenRouter key, an S3 secret, a private path, or holdout content.
8. With `HERMES_CHAT_ENABLED=false` — **the default** — no chat gateway process exists, nothing listens on 8643, the page shows the disabled state and makes no chat request, and the API returns 503 with the disabled sentence. Every other screen works exactly as before.
9. With chat on but misconfigured (no chat key, or no model), the API and the page say which variables are missing, and that message differs from the disabled one.
10. Deleting a thread removes its conversation and messages, leaves the runs it started intact, and leaves audit rows that still record who did what, flagged `thread_deleted`. Audit rows disappear only when they are older than `ADMIN_CHAT_AUDIT_RETENTION_DAYS`.
11. With chat disabled, the retention prune still removes audit rows past the window; neither the audit route nor the prune depends on `HERMES_CHAT_ENABLED`.
12. `uv run --env-file .env pytest -m "not network and not e2e"`, `uv run ruff check`, `uv run ruff format --check`, `bun run typecheck`, `bun run lint`, and `bun run test` are green, with the same known `tests/test_incremental_index.py` exception the Hermes PRD already carries if it is still open.

## Dependencies & assumptions

### Dependencies

- `tasks/prd-hermes-optimizer.md` US-001 through US-010 are in the tree on `feat/hermes-optimizer`: the container, the two profiles, `start_optimization_run`, the cancel route, the step log, `hermes_run_id`, and, since US-010 (`d068bba`), Hermes as the only optimization agent with OpenHands removed. `OPTIMIZATION_AGENT` is `Literal["hermes"]` defaulting to `hermes`, and `AgentUnavailable` lives in `optimization.py`.
- `src/deepfix_rag/optimization.py` stays the only owner of run state, and `src/deepfix_rag/promotion.py` stays the only promoter.
- `optimization-logs/<run_id>.log` remains the step-log location and its redaction remains in force.
- The web app's `/api` proxy (`web/src/server.ts`) streams response bodies unchanged, which it does today.
- `@assistant-ui/react` and `@assistant-ui/react-markdown` stay in `web/package.json` at their current ranges.

### Assumptions (labelled)

- **(assumption)** The pinned image can run a second, **standalone** profile gateway on 8643 next to the optimization gateway on 8642 in one container, and boot can start it. CHAT-001 confirms it. Two fallbacks, in order: (a) if the pin cannot start a standalone profile's listener, add the chat profile to the optimization gateway's multiplex routing and accept that chat is unavailable when chat is off only because the profile is deleted from `profiles/` — this contradicts D-1's “no memory when off”, so it needs your sign-off first; (b) a second `hermes` service on the same image with `HERMES_HOME` pointed at a chat-only volume, which satisfies D-1 exactly and costs a second container entry. FR-001 only requires a private, keyed endpoint that does not run when chat is off, so (b) is compatible without a decision change.
- **(assumption)** `gateway.standalone: true` is honoured by the pinned v0.22.7 and stops the default gateway from serving `/p/chat/...`. Read from the provider's multi-profile docs, not measured here. CHAT-001 proves it with the negative test on 8642.
- **(assumption)** `POST /v1/chat/completions` on the chat profile emits `chat.completion.chunk` frames plus `event: hermes.tool.progress` for its toolsets, as the verified facts table in the Hermes PRD records for that pin. The stream relay treats unknown frames as opaque text and does not depend on their schema.
- **(assumption)** The API keeps running one uvicorn worker (`Dockerfile` CMD has no `--workers`). The rate limit is computed from persisted rows, so it stays correct if that changes; the in-process subscriber registry for a live resume is per process, and the resume endpoint degrades to “stored text plus terminal status” when it is not the owning process.
- **(assumption)** A chat turn is short by design. Nothing in the profile allows shell work, so a 300-second cap does not cut off a legitimate operation; a task longer than that is a job, and the job has its own timeout.
- **(assumption)** Audit volume stays small (tens of rows per admin per day), so the 14-day prune of D-6 keeps the table within a few thousand rows and needs no archive, partitioning, or index beyond the retention delete. If volume is far higher, the prune stays correct and only the row count grows.
- **(assumption)** Hermes's chat completion does not need the DeepFix `run_id`; the trace correlation is the chat thread and turn id, not an optimization run id.

## Risks & mitigations

1. **A prompt-injected reply proposes an action the admin misreads.** The allowlist has two kinds, the card states the effect in one sentence, the confirm is a deliberate click, the run still passes `start_optimization_run`'s skip rules, and promotion is not an action at all. The audit row records who confirmed. Residual risk: an admin confirms a start they did not want; `cancel_optimization_run` is the remedy and nothing is promoted without the gate.
2. **The chat profile leaks the optimization profile's credentials through the shared volume.** Both profiles run as the same `hermes` user on `/opt/data`. Mitigation: the chat profile has no terminal and no file toolset, is given no `OPENROUTER_API_KEY`, `S3_*`, or product key at boot, and `skills.external_dirs` is not attached to it. CHAT-001 asserts the env split and the toolset list. Residual risk: a future toolset addition could re-open it; the test is the guard.
3. **Two gateways exhaust the 4 GB container during a run.** An optimization job already peaks near 3.9 GB on a 500-document slice (`tasks/prd-hermes-hpo-rounds.md`). Mitigation: the chat gateway exists only when chat is on (D-1), so a deployment that does not want it pays nothing; when it is on, two concurrent turns maximum, one turn per admin, no shell in the profile, a 300-second turn cap, and a run panel that polls the API rather than streaming from Hermes. CHAT-012 records memory on and off and reports it; no cap is raised.
4. **The chat profile is silently served by the optimization gateway instead of standing alone.** If `gateway.standalone` is not honoured, `gateway.multiplex_profiles` would serve `/p/chat/...` on 8642 and the chat profile would cost memory with chat off, quietly breaking D-1. Mitigation: boot refuses to write a chat config without the key, CHAT-001 asserts the negative case on 8642, and CHAT-012 checks the process list. Residual risk: the pin may ignore the key; the fallback in Assumptions (b) is the remedy.
5. **A long reply looks dead and the admin reloads repeatedly.** Mitigation: SSE keepalives are forwarded, the composer becomes a stop button immediately, the partial text is persisted so a reload resumes, and the resume endpoint degrades safely after a restart.
6. **The proxy buffers and the stream arrives all at once.** The web server relays the body, and FastAPI's `StreamingResponse` plus `X-Accel-Buffering: no` is what prevents an intermediary from accumulating. CHAT-003 asserts the relay emits the first frame before the upstream stream closes, against a local server that deliberately delays its second chunk, so a buffering regression fails the test. CHAT-012 checks the same through the deployed proxy.
7. **A delete is mistaken for a full erase, and an admin expects their words to be gone everywhere.** Mitigation: D-4 states exactly what is deleted and what survives; the delete dialog says so in the UI; the audit row holds no message text and never did. Residual risk: an admin reading “delete” may still expect the audit entry to vanish. The flagged alternative in D-4 is the one-line change if that expectation should win.
8. **Audit and transcript tables grow without limit.** Mitigation: a bounded read API, and a retention prune by age (`ADMIN_CHAT_AUDIT_RETENTION_DAYS`, default 14, D-6) that runs from the existing scheduler tick so it keeps working with chat off. Transcript tables are bounded by thread delete; no message text is duplicated into the audit table.

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
| FR-022, FR-025, FR-027, FR-028 | G1 |
| FR-023 | G3 (the Admin-section boundary) |
| FR-026 | G1, G4 (no regression) |
| FR-029 | G5, D-1 |
| FR-030 | G6, NFR-5 (the audit trail is bounded in time, D-6) |

No FR is unlinked to a goal, and no goal is without FRs.

## Story to FR map

| Story | FRs |
| :--- | :--- |
| CHAT-001 | FR-001, FR-002, FR-020, FR-029 |
| CHAT-002 | FR-010, FR-016, FR-018, FR-030 (the prune function) |
| CHAT-003 | FR-004, FR-005, FR-007, FR-019, FR-021, FR-028 |
| CHAT-004 | FR-006 |
| CHAT-005 | FR-008, FR-009, FR-010, FR-011, FR-012 |
| CHAT-006 | FR-017, FR-018, FR-030 (the scheduler wiring) |
| CHAT-007 | FR-013, FR-014, FR-015 |
| CHAT-008 | FR-016 |
| CHAT-009 | FR-003, FR-022 |
| CHAT-010 | FR-022, FR-023, FR-025, FR-026, FR-027, FR-028 |
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
- **Web.** TanStack Start. `src/routes/admin.tsx` is the admin route pattern (loader returns `null` for a non-admin, then the component renders `OptimizationDenied`). `src/components/layout/navigation.ts` gates `/history` and `/admin` by role via `ADMIN_PATHS`. `src/services/optimization.functions.ts` shows the `requireRole("admin", …)` server-function pattern. `src/server.ts` proxies `/api/*` to FastAPI, strips `cookie` and `authorization`, adds the identity bearer, and streams `upstream.body` back. `src/features/chat/chat-page.tsx` already uses `AssistantRuntimeProvider` with `useExternalStoreRuntime` and `ThreadPrimitive`, keeping threads in `localStorage` under per-user keys. After US-010 (`d068bba`) the Optimization screen's unavailable sentence is the Hermes one.
- **Profile multiplexing.** On v0.22.7, `gateway.multiplex_profiles` defaults to `true`, and a multiplexed gateway serves every live profile under `profiles/` through `/p/<profile>/v1/...` on the default listener, authenticating each prefix with that profile's own `API_SERVER_KEY`. A profile opts out with `gateway.standalone: true` in its own `config.yaml` and then runs its own listener on its own port. This is why D-1 requires the chat profile to be standalone: without it, chat would be served by the optimization process and would not be free when off. Source: the provider's multi-profile gateway and API server docs, read 2026-11-13; not measured on this VM.
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
| 1. Server surface | CHAT-001, CHAT-002 | The chat profile answers on 8643 while enabled and starts nothing while disabled, and the tables exist. |
| 2. Conversation | CHAT-003, CHAT-004, CHAT-005 | A turn streams through the API against a local stand-in and survives a reload. |
| 3. Safety and jobs | CHAT-006, CHAT-007, CHAT-008 | Limits, audit, the retention prune, action cards, and the run panel work with real domain calls. |
| 4. UI | CHAT-009, CHAT-010 | The admin chats in the browser, sees the disabled state when chat is off, and a member is denied. |
| 5. Evidence | CHAT-011, CHAT-012 | A trace exists in `deepfix_hermes_chat`, and the deployed pass is recorded. |

## Success Metrics

- An admin answers “why did the last run reject?” in the chat without opening MLflow, in one turn.
- An admin starts a run from an action card, watches it to a terminal status in the thread, and reloads the page mid-run without losing the panel.
- Zero credentials in any browser payload, verified by inspecting the network panel in CHAT-012 and by the `model`-field-stripping test in CHAT-003. A source grep proves only that a literal is absent, not that a runtime payload is clean, so it is not the evidence.
- Zero holdout content in any chat response, audit row, step tail, or trace, verified by a search for a known holdout question.
- A stopped turn keeps its partial text; a retried turn produces a new reply; a resumed turn shows no duplicated text.
- The 31st turn in a five-minute window is refused with a readable sentence.
- Peak container memory during chat plus a run stays under 4 GB.
- With chat off, the container runs exactly one gateway process and the page makes zero chat requests.
- Deleting a thread leaves its started runs and its audit trail intact, and the audit view flags the gone thread.
- The Optimization screen and the Ask screen behave exactly as before, with their existing tests unchanged and green.

## PRD quality gates (self-check)

- [x] Every **Must** FR has testable acceptance wording. Each names a route, a setting, a status code, a frame, or a visible control.
- [x] No duplicate FRs. FR-021 (redaction) and FR-006 (digest content) are adjacent but distinct: one is about every outbound string, the other about what enters the prompt.
- [x] Non-goals and Won't do not contradict a Must FR. The chat proposes and confirms; the promotion FR-015 forbids the promote path, and no Must FR requires it.
- [x] Nouns are glossary-consistent: thread, turn, job/run, action card, step line, gate, holdout, chat profile.
- [x] Security and privacy are covered (NFR-4, NFR-5, FR-002, FR-003, FR-015, FR-021) because this feature carries authn, credentials, and a data boundary.
- [x] Every FR maps to a goal in Traceability, and each is reachable from a story in the Story to FR map.

## Validation (validate-prd)

Run four times. The first pass validated the original draft. The second (2026-11-13, after the D-1…D-5 decisions) was an independent review against the `validate-prd` dimensions and found four blockers. The third verified those fixes and found six consistency gaps the fixes had not been carried through to. The fourth (2026-11-14, after D-6) verified the audit-retention decision. All are closed. Verdict: **Pass with actions**, and the only remaining actions are the three open questions, each with a recommended default implemented in a story; neither remaining item is a table, route, or setting decision.

### Second pass: the four blockers, and how they were closed

1. **FR-003 contradicted FR-007 (Must vs Must).** FR-003 said the browser must never receive a model id; FR-007 relayed frames verbatim, and `chat.completion.chunk` frames carry `"model"`. Resolved by naming the single exception: the relay deletes the `model` key from each JSON frame and re-serialises, and FR-003, FR-007, and CHAT-003 all say so, with a test that the forwarded stream has no model id but keeps the text, `finish_reason`, and tool progress.
2. **The API container could not receive its own settings.** `docker-compose.yml` gives `backend-api` an explicit environment allowlist and no `env_file`, so the chat variables would have existed only on the `hermes` service. FR-001, FR-017, FR-020, and FR-028 would have been unreachable on the deployed stack. CHAT-001 now adds every `HERMES_CHAT_*`, `ADMIN_CHAT_*`, and `MLFLOW_HERMES_CHAT_EXPERIMENT` name to both `Settings` and the `backend-api` environment, and adds a test that a setting cannot be added without being plumbed.
3. **The disabled UI state had no specified data source.** It offered “the route returns 503, or a flag is read”, which was either a chat call (breaking “zero requests”) or a source nobody named — and a new status route would have broken the locked eleven-route list. Resolved: `GET /admin/optimization` gains `chat_enabled`, read from `settings.HERMES_CHAT_ENABLED` in the existing `_status` handler, and the `/admin/chat` loader reads it from the optimization query it already has.
4. **A CHAT-001 criterion could not pass.** It promised `test_chat_profile_env_is_replaced` would stay green while gaining an assertion that the chat key is present, but that test asserts `"API_SERVER_KEY" not in text` for the chat `.env` — the exact thing the story reverses. The story now states the test must change, names the four assertions that replace it, and adds the chat key to the test's `_env()` fixture.

### Second pass: should-fix and nice-to-have, also applied

- **The audit route survived a disable.** Gating *every* `/admin/chat/*` route on `HERMES_CHAT_ENABLED` would have hidden the security record exactly when chat was turned off. `GET /admin/chat/audit` is now exempt. A third pass found the follow-through still missing in CHAT-012, which is fixed below.
- **Delete order disagreed with itself.** D-4 listed the order parent-first and CHAT-002 children-first. Both now use one order — action cards, messages, turns, thread-run links, thread row — and FR-010 carries the same wording.
- **The delete dialog understated the delete.** It now names the conversation, its messages, and pending action cards, and says the record of a started run is kept.
- **`hermes_session_id` was a double-history hazard.** The column and the `X-Hermes-Session-Id` header are both dropped, with the reason stated: the proxy is the only source of history, and a Hermes-side session plus a replayed history would double the conversation.
- **The hand-written SSE module would fail `api-boundary.test.ts`.** That test fails any `fetch(` outside the generated client, `server.ts`, and `api.ts`. CHAT-009 now names `web/src/services/admin-chat-sse.ts` as a fourth `EXEMPT` entry — **as the relative path `join("services", "admin-chat-sse.ts")`, because the test matches with `EXEMPT.has(relative(SRC, file))`**, so a bare filename would silently miss.
- **“No memory” was unfalsifiable as prose.** FR-029 and D-1 define it as exactly one gateway process and nothing bound to 8643, with RSS reported for information only because summed RSS double-counts shared pages in one container. The process match is on arguments and profile, not a literal name, since the pinned image runs the gateway through a Python interpreter.
- **Open question 3's default was implemented nowhere.** CHAT-007 now has an acceptance criterion and a test that a card can be confirmed while a turn is streaming.
- **The 503 distinction relied on string matching.** FR-028 and CHAT-003 add stable `code` values. A third pass found the mechanism unspecified — `ErrorResponse` is `detail: str` only — so CHAT-003 now defines the nested `detail` object, the `ErrorResponse` union, and the `api-result.ts` branch.
- **`system_prompt` could not emit the ids CHAT-011 needs.** It takes `thread_id` and `turn_id` as explicit arguments.
- **`ensure_hermes_trace_experiment` hardcodes one experiment name.** It gains a `name` parameter for the chat call.
- **FR-024 was Should while D-5 calls the experiment settled.** Promoted to Must. FR-025 was also promoted, because G1 and NFR-6 promise accessibility and it is the only FR that delivers it.
- **J2 used the API path rather than the `/api` prefix.** Normalised.
- **`HERMES_CHAT_API_SERVER_KEY` was described as newly added** when it is already in `compose.hermes.yml` and `example.env`. Reworded.
- **A source grep cannot prove a runtime payload is clean.** The success metric now cites the network inspection and the `model`-stripping test instead.
- **FR-003's rationale still said “verbatim”.** Restated as “everything preserved, one key removed”.

### Third pass: what the follow-through missed

A third review checked whether the second pass's fixes were applied consistently rather than only where they were first raised. Six gaps, all fixed:

1. **CHAT-012 required the opposite of CHAT-003.** It said every chat route answers the disabled 503, while CHAT-003 requires the audit route to answer 200. A criterion that fails when the operator does the right thing. D-1 and CHAT-001 carried the same stale phrase; all three are corrected and CHAT-012 now checks audit 200 and delete 204.
2. **The env plumbing stopped at the API.** `chat_profile_config` reads `HERMES_CHAT_MODEL` and boot reads `MLFLOW_HERMES_CHAT_EXPERIMENT` **inside the `hermes` service**, whose environment is also an allowlist. Both are now added there, and the plumbing test covers both services.
3. **`${VAR:-}` breaks typed settings.** Verified locally against this repo's pydantic-settings: `FLAG=''` on a `bool` field raises `bool_parsing`, `N=''` on an `int` raises `int_parsing`. Every typed chat variable therefore takes a non-empty Compose default (`${HERMES_CHAT_ENABLED:-false}`), and a test composes with all chat variables unset and asserts `Settings()` builds.
4. **A new required field breaks a web fixture.** `ApiModel` sets `json_schema_serialization_defaults_required=True`, so `chat_enabled` becomes required in `OptimizationStatusResponse`, and `optimization-page.test.tsx`'s `status()` fixture stops compiling. CHAT-010 now owns that edit explicitly.
5. **Thread delete was unreachable when chat was off.** Every non-audit route 503'd, so an admin could not remove conversation text after a disable — the opposite of what D-4 and NFR-5 want. `DELETE /admin/chat/threads/{thread_id}` is now the second exempt route, and the check order is specified per route class.
6. **Stale comments in the tree.** `example.env:103`, `compose.hermes.yml:4`, `docker/hermes/Dockerfile:37`, and the chat `SOUL.md` text all still say the profile is not started or is “the future admin chat”. CHAT-001 now updates all four, with a grep test.

Also tightened: a non-JSON `data:` frame is dropped rather than forwarded (an opaque payload could carry the `model` id FR-003 forbids); `admin-chat-sse.ts` must be exempted by relative path; and the `code` field's mechanism is specified.

### Fourth pass (2026-11-14): the retention decision

Fadel closed the last open decision. D-6 records it, FR-030 carries it, and four things had to change consistently rather than in one place:

1. **Retention was a literal waiting to happen.** It is now `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (default 14), plumbed like every other chat setting — `Settings`, the `backend-api` Compose environment with `${ADMIN_CHAT_AUDIT_RETENTION_DAYS:-14}`, `example.env`, and CHAT-001's plumbing test. `0` or negative disables the prune, which is the only way to keep rows forever.
2. **The prune needed a home, and the tick needed a wake-up cap.** It runs from the existing `OptimizationScheduler._loop` tick. That loop already wakes hourly when the schedule is paused but sleeps the full gap to the next run otherwise, so a prune hung off it alone would run about once a day; the wait is now `min(gap, 3600)` in every case. Rejected: `fail_stale_runs` (a request path, so a `DELETE` on every status poll), a startup-only prune (an API that never restarts never prunes), and a separate daemon thread. The scheduler tick also makes "keeps working when chat is disabled" free, since the scheduler is not gated on `HERMES_CHAT_ENABLED`.
3. **Delete and retention had to be told apart.** D-4 settled that a delete never removes an audit row; FR-018 now says a row goes only by age, and D-4's disagreement paragraph is replaced by the decision rather than left as a recommendation.
4. **Assumption A6 and risk 8 still described an unbounded table.** Both now describe the prune, and the open-questions list no longer carries retention. The release acceptance criteria gained one item (the prune works with chat off) and the traceability and story-FR maps gained FR-030, in CHAT-002 (the prune function) and CHAT-006 (the scheduler wiring).

### Scores (final, after the fourth pass)

| Dimension | Status | Evidence |
| :--- | :--- | :--- |
| Completeness | Green | Overview, goals, six decisions, non-goals (three tiers), personas, four journeys, 12 stories, 30 FRs, 9 NFRs, release acceptance, dependencies, assumptions, 8 risks, 3 open questions with defaults, glossary, traceability, story map. |
| Consistency | Green | D-1…D-6 are cited in the stories, FRs, risks, glossary, journeys, and metrics; delete order, the retention window, the model-id rule, and the 503 codes match wherever they appear. |
| Testability | Green | Every Must FR names a route, a setting, a status code plus `code`, a frame, a process/port check, or a visible control. The one wall-clock target is a CHAT-012 measurement, not a test. |
| Traceability | Green | 30 FRs, all in the FR table and the story map, no gaps or duplicates; every FR maps to a goal; every goal has FRs. |
| Feasibility | Amber | Unchanged and deliberately so: two gateways in one 4 GB container and `gateway.standalone` on the pinned image are unmeasured here, recorded as A1/A2 with two fallbacks. CHAT-001 and CHAT-012 turn them into measurements. |
| Security & privacy | Green | Admin on all eleven routes with a check order specified per route class, the audit and delete routes deliberately reachable when chat is off, `_SECRET_ENVS` extended to the chat key, `model` stripped from frames, holdout excluded, audit without message text and pruned by age even with chat off, prompt-injection delimiters. |

### Still Amber, and why that is correct

Feasibility is the only Amber and it cannot be closed by editing: it needs a container runtime. The PRD states the claim, the assumption, the fallback, and the test that settles it. That is the right state for a plan.

### Claims this VM could not verify

- Every Hermes v0.22.7 behaviour: `gateway.multiplex_profiles` defaulting to true, `gateway.standalone: true` as the opt-out, `/p/<profile>/v1/...` routing on 8642, the exact start command for a standalone profile, and the `chat.completion.chunk` / `hermes.tool.progress` frame shapes. All are read from the provider docs and labelled as assumptions A2/A3; CHAT-001 proves the profile behaviour with a negative test on 8642.
- Two gateways inside a 4 GB limit, and whether Dokploy exposes `docker stats` / `ps` as CHAT-012 assumes.
- `@assistant-ui/react` 0.15.28's `ThreadListPrimitive`, `MessagePrimitive`, `ActionBarPrimitive`, `onCancel`, and `onReload`: `web/node_modules` is absent, and the existing Ask page only uses `AssistantRuntimeProvider`, `ComposerPrimitive`, `ThreadPrimitive`, and `useExternalStoreRuntime`. CHAT-010 keeps this as a verification note rather than a claim.

Verified against the tree during the second pass: chat port 8643, `CHAT_API_KEY_ENV`, `CHAT_API_TOOLSETS`, the stopped `gateway_state.json`, the chat `.env` contents and the `API_SERVER_KEY not in text` assertion, `validate_api_key`'s 16-character floor, `_SECRET_ENVS` lacking the chat key, `STEP_PREVIEW_LIMIT = 500`, the step-log path, `apply_candidate` as the only champion writer, `feedback.is_holdout`, the seven routers and `recover_running_runs`, the `backend-api` env allowlist with no `env_file`, `AppSidebar`'s `startsWith` check, `ADMIN_PATHS` / `AppPath`, `admin.tsx`'s loader-plus-denied shape and missing `<Outlet />`, `api-proxy.ts` header stripping, `server.ts` streaming `upstream.body`, `api-boundary.test.ts`'s `EXEMPT` set, `d068bba` (US-010), and the absence of `rate_limit` / `audit` / any SSE route in `src/`.

Next step after this PRD is accepted: **`create-story`** for CHAT-001. Its acceptance criteria are now internally consistent, which is the one thing that had to hold before story breakdown.

## Open questions / assumptions

Five questions from the first draft are now decisions and have moved to the **Decisions** section: the opt-in switch and the no-memory guarantee (D-1), the disabled UI state (D-2), the model fallback (D-3), hard delete with surviving audit rows (D-4), and the separate chat experiment (D-5). Audit retention was the sixth open question and is now **D-6** (14 days, `ADMIN_CHAT_AUDIT_RETENTION_DAYS`). Two open threads from the earlier drafts are closed: delete never removes audit rows (Fadel, 2026-11-14), and retention is bounded rather than unbounded.

### Still open, with a recommended default

Each of these is implemented the recommended way unless Fadel says otherwise. None of them blocks CHAT-001, and none is a decision that changes a table, a route, or a setting — they are measurements and future additions.

1. **Does Hermes abort an in-flight chat turn when the client disconnects?**
   - *Facts:* the runs API has `POST /v1/runs/{run_id}/stop`; the chat completions surface documents no stop route. CHAT-001 can measure it in one request by closing the socket mid-turn and watching the container's token usage or the gateway log.
   - *Recommended default, implemented:* close the upstream stream, record `cancelled`, keep the partial text, and discard the rest. This is the safe behaviour and needs no Hermes feature. Cost if the turn keeps running server-side: the model tokens for the remainder of that turn, and the concurrency slot until it settles.
   - *If the answer is “it keeps running”:* FR-008 gains a follow-up that calls a stop route if one exists on this pin; if none exists, the turn cap (300 s) is the only bound and the risk is accepted and recorded.
   - *Owner:* whoever runs CHAT-001; the measurement goes in that story's verification note.

2. **Should the chat be able to page one run's step log on demand, instead of the 50-line tail?**
   - *Recommended default, implemented:* no. The tail (`ADMIN_CHAT_STEP_TAIL`, 50 lines) is enough to answer “what is it doing now”, and the full log is already available to an operator on the server. A paged route is one more admin endpoint and one more place a step line could be over-read.
   - *If it is wanted later:* add `GET /admin/chat/runs/{run_id}/steps?limit&before` with the same bounded, redacted reader, capped at 500 lines per call. It is an additive story, not a redesign.

3. **Should a confirmed `start_optimization_run` be allowed while a chat turn is still streaming?**
   - *Recommended default, implemented:* yes. The conversation plane and the job plane are separate by design (see “What the two planes are”), and `start_optimization_run` already refuses a second concurrent run with `RunInProgress`. Blocking the card until the reply finishes would mean an admin who wants to start a run must wait for a long answer to end, for no safety gain.
   - *If it is unwanted later:* disable the card's Confirm button while the thread's turn is `running`. One UI condition, no API change.

### Assumptions carried

Each is also labelled where it matters in “Dependencies & assumptions”.

- A1. The pinned image can run a second, standalone profile gateway on 8643 next to 8642, and boot can start it. If not, the fallback is a second `hermes` service with a chat-only home.
- A2. `gateway.standalone: true` is honoured on v0.22.7, so the optimization gateway does not serve `/p/chat/...`. CHAT-001 proves it with a negative test.
- A3. The chat profile speaks `chat.completion.chunk` plus `hermes.tool.progress`; unknown frames are relayed as opaque text.
- A4. One uvicorn worker, so an in-process resume registry is sufficient; the resume endpoint degrades to stored text otherwise.
- A5. A 300-second turn cap is far above a legitimate chat turn, because the chat profile cannot run shell work.
- A6. Audit volume stays small enough that the D-6 prune (14 days by default) keeps the table bounded without an archive: rows are small and the read is bounded.
- A7. Chat correlation is the thread and turn id, not an optimization `run_id`.

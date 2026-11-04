# PRD: Hermes Optimization Sandbox

## Introduction

DeepFix optimization today is a background thread in the API process. That thread starts an OpenHands child (`scripts/openhands_runner.py` via `src/deepfix_rag/agent_process.py`), waits up to `OPTIMIZATION_RUN_TIMEOUT_HOURS` (default 3), and kills the process group on timeout. The API owns the run row, the dev/holdout split, and promotion. The agent only proposes a candidate.

This feature replaces that child with [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research), running in its own container and reached over its OpenAI-compatible API. The API still decides whether a run starts, still scores holdout, and still promotes. Hermes proposes a candidate config and produces dev-set evidence.

This document is the plan. It does not implement the feature.

### Clarifying questions, answered from the owner's decisions

The planning skill asks these before a PRD is written. The owner already answered them:

1. What is the primary goal?
   A. Give the existing optimization loop a remote sandbox that can run for hours and that a future admin chat can also drive.
   B. Rebuild optimization inside Hermes, including the holdout gate.
   C. Add a public chat product on top of Hermes.
   Answer: **A.**

2. Where does the agent run?
   A. In its own Compose service, internal network only, non-root, with resource limits, the `deepfix` CLI, and the repo `SKILL.md` guides.
   B. In the API container, as today.
   C. As a public Dokploy domain.
   Answer: **A.**

3. How does a multi-hour run stay alive?
   A. One chat HTTP request held open for the whole run.
   B. The chat surface is the control and conversation channel, and a small job API (start, status, cancel, and optionally events and logs) carries the run. The API tracks a run id, polls status, cancels, times out at 3 hours, and recovers when either container restarts so no row stays `running`.
   C. Hermes cron (`/api/jobs`) replaces the API scheduler.
   Answer: **B.** The nightly schedule stays in the API. A later note makes events, step logs, MLflow traces, heartbeats, and Hermes container logs required rather than optional. See US-007.

4. Who may see holdout and who may promote?
   A. The API only. Hermes receives the dev set, writes a candidate, and cannot promote.
   B. Hermes scores holdout and promotes when its own gate passes.
   Answer: **A.**

5. How does Hermes read and write LanceDB, and which model does it call?
   A. The RustFS S3 endpoint, through the `S3_*` environment variables the CLI already uses, with no shared volume, and a scoped key. Models go through OpenRouter. The product model is `LLM_MODEL=anthropic/claude-haiku-5.5`, which this repo routes through LiteLLM with an `openrouter/` prefix and `drop_params`.
   B. A shared Docker volume of `/data/workspace`, and the OpenHands model `DEEPFIX_LLM_MODEL_NAME`.
   Answer: **A.**

6. What happens to OpenHands?
   A. The child process and the Compose `openhands` service are replaced, with the current path kept behind a flag until a Hermes run is proven.
   B. Delete OpenHands in the same change that adds the client.
   Answer: **A.**

## Goals

- Run Hermes as a Compose service on the internal network, as user `hermes`, with CPU and memory limits, the same `deepfix` revision the API is running, and the repo skills installed read-only.
- Drive a multi-hour optimization from the API with Hermes `POST /v1/runs`, status polls, and `POST /v1/runs/{run_id}/stop`, so a single chat request is never held for 3 hours.
- Keep the dev/holdout split, the quality gate, and promotion in the API. Hermes never receives holdout rows and never writes `configs/champion.yaml`.
- Hand LanceDB to Hermes through RustFS (`S3_ENDPOINT_URL` and an `s3://` index URI). Do not mount `api_data` or `rustfs_data` into the Hermes container.
- Call OpenRouter with provider `openrouter` and model id `anthropic/claude-haiku-5.5`. Keep the product `LLM_API_KEY` out of the Hermes container.
- Leave the OpenHands child in place behind `OPTIMIZATION_AGENT=openhands` until the Hermes path has passed the Dokploy end-to-end test. The default stays `openhands` until that test passes.
- On a restart of the API container or the Hermes container, leave no `optimization_runs` row in `running`.
- Make a running optimization visible. In production the OpenHands child writes nothing to the `backend-api` container logs: a run shows `MLflow run created` and `POST /admin/optimization/run 200`, then silence until the 3-hour timeout. Hermes must emit step lines on the API logs, agent and LLM traces in MLflow, a heartbeat that fails a stalled run well before that timeout, and the agent's stdout and stderr on the Hermes container logs.

## User Stories

Stories are ordered in the phases in Technical Considerations. Each story is one implementation session. This PRD does not check them off.

### US-001: Hermes sandbox image and Compose service
**Description:** As an operator, I want a Hermes container that is not the API container, so a hung agent cannot take down Search and Ask.

**Acceptance Criteria:**
- [x] A new image, built from `nousresearch/hermes-agent` pinned by digest, installs `deepfix` into a Python 3.12 virtualenv on `PATH` (the official image's own environment is Python 3.14 and `/opt/hermes` is read-only to the runtime user). The virtualenv is not `/opt/hermes/.venv`.
- [x] The image copies `.agents/skills/` to a root-owned directory that the `hermes` user cannot write, and sets `skills.external_dirs` to that directory.
- [x] The image's `config.yaml` sets `model.provider: openrouter`, `model.default: anthropic/claude-haiku-5.5`, `terminal.backend: local`, `terminal.timeout` high enough for one `deepfix index` or `deepfix eval` (starting point 7200 seconds; confirm on the pinned image), and `approvals.mode: off` inside this sandbox only. `approvals.deny` still blocks the documented hard patterns the operator adds (force-push, pipe-to-shell).
- [x] The process runs as the image's `hermes` user. The container has no Docker socket, no published host ports, and no `HERMES_DASHBOARD`.
- [x] Compose memory limit is 4 GiB and CPU limit is 2, as the starting point the Dokploy test may raise. `restart: unless-stopped`.
- [x] `API_SERVER_ENABLED=true`, `API_SERVER_HOST=0.0.0.0`, and `API_SERVER_KEY` is required and at least 8 characters. The key is injected from the environment, never baked into the image.
- [ ] `GET http://hermes:8642/health` returns `{"status": "ok"}` from `backend-api` on the Compose network. The host cannot open port 8642. The `chat` profile's port 8643 is also unpublished. Its contract is in Design Considerations. It is not started as the optimization agent.
- [x] `GET /v1/capabilities` on that container advertises `run_submission`, `run_status`, `run_stop`, and `run_events_sse` before the image digest is committed. An image that lacks them is not a valid pin.
- [x] `uv run ruff check` and `uv run ruff format --check` pass for any Python added to the image build.

Verification note (2026-11-04), for the criteria above:

- Pin is `nousresearch/hermes-agent` v0.22.7, linux/amd64 digest `sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c`. `GET /v1/capabilities` on that image, before the digest was written into `docker/hermes/Dockerfile`, returned `run_submission`, `run_status`, `run_stop`, and `run_events_sse` all true. The built image returned the same four flags. `v2026.6.19` was not used.
- Inside the running container, `deepfix` resolves to `/opt/deepfix/.venv/bin/deepfix`, `sys.version` is 3.12.15, and `sys.prefix` is `/opt/deepfix/.venv`. `command -v python` remains `/opt/hermes/.venv/bin/python`. `deepfix --help` lists the CLI commands.
- `/opt/deepfix/skills` is root `0755` and `SKILL.md` files are root `0444`. `docker exec --user hermes` cannot append to a skill file. `skills.external_dirs` is that directory. The running `config.yaml` has `model.provider: openrouter`, `model.default: anthropic/claude-haiku-5.5`, `terminal.backend: local`, `terminal.timeout: 7200`, `approvals.mode: off` (a string, not a boolean), and deny globs for `git push --force*` and pipe-to-shell (`*curl*|*sh*`, `*wget*|*sh*`, and the bash variants).
- On this pin the documented `terminal.timeout` is not the effective ceiling. `FOREGROUND_MAX_TIMEOUT` defaults to 600 (`TERMINAL_MAX_FOREGROUND_TIMEOUT`) and `timeouts.tools.sequential_call` defaults to 420. Both would cut off `deepfix index` or `deepfix eval` before 7200 seconds. The image sets the foreground cap to 7200 and the tool deadlines to 7500. Linux `MAX_SAFE_TIMEOUT_S` is 365 days, so 7200 is under the platform clamp.
- The gateway process (`hermes gateway run`) is uid 10000 (`hermes`). s6 PID 1 stays root; that is how this image drops privileges. The only mount is `hermes_data:/opt/data`. `docker port hermes` is empty. `curl` to `127.0.0.1:8642` and `:8643` from the host is connection refused. `HERMES_DASHBOARD=0` and nothing listens on 9119. Inspected limits are 4294967296 bytes and 2 CPUs, restart policy `unless-stopped`.
- `API_SERVER_KEY` is required at container start and is not in the image. This pin refuses keys shorter than 16 characters (the PRD's "at least 8" is the older docs floor and is not enough here). A short key and `change-me-please` exit 1. `LLM_API_KEY` in the environment also exits 1. s6 ignores a failing cont-init script unless `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`, which this image and Compose set. The healthy container's key length was 64, injected from the environment.
- The unchecked health criterion: inside the container, `GET /health` returns `{"status": "ok", "platform": "hermes-agent", "version": "0.22.7"}`. The chat profile is created with port 8643 in its own `.env`, `approvals.mode: smart`, no terminal or file toolset, no OpenRouter key, and `gateway_state` `stopped`. The reconciler logs `profile=chat prior_state=stopped action=registered` and does not start it. What this VM could not show is a request from `backend-api` (or any second container) to `http://hermes:8642`. DNS on `deepfix-rag_default` resolves `hermes` to `172.18.0.2`, and the docker host can open that bridge address, but container-to-container TCP on the bridge times out. The same timeout happens between two unrelated containers, so it is this VM's bridge, not a published port. The `backend-api` image was not built here.
- `uv run ruff check` and `uv run ruff format --check` passed. `uv run --env-file .env pytest -m "not network and not e2e"` was 220 passed, 22 deselected, and one failure: the known `tests/test_incremental_index.py::test_startup_resumes_indexing_and_deleting`.
- Not started: US-003 through US-010. `OPTIMIZATION_AGENT` is still unset, so the API still runs the OpenHands child. The optimization profile's toolsets are not narrowed (US-006). The `deepfix` shim and the otel plugin are not installed (US-007).

### US-002: Run bundle on a scoped S3 prefix
**Description:** As an AI engineer, I want Hermes to read the corpus, the dev set, and the champion index through RustFS, so the API volume is not mounted into the sandbox.

**Acceptance Criteria:**
- [x] Before `POST /v1/runs`, API code writes a fresh prefix `s3://deepfix-index/optimization/runs/<run_id>/` (the bucket in `S3_URI`) containing `input/champion.yaml`, `input/feedback_dev.jsonl`, `input/corpus/` (the workspace corpus files), and `input/ledger.jsonl` when the workspace ledger exists. The champion YAML's `retrieval.lancedb_index_dir` points at `input/champion-index/`, which is a copy of the serving index made by the API with the root key. `data.path_or_repo` in that YAML is the in-container path the sync helper will use.
- [x] The prefix is unique per `run_id`. A later run never opens an earlier run's LanceDB directory.
- [x] Holdout rows are not uploaded. A test lists every object written for a run and asserts none of the holdout query texts or holdout query ids appear.
- [x] Hermes receives one IAM access key whose policy is the prefix document in Technical Considerations. It can list, get, put, and delete only under `optimization/runs/*`. It cannot get the rest of `deepfix-index`, and it is not the root key (`RUSTFS_ACCESS_KEY` / `S3_ACCESS_KEY_ID`). The API root key writes inputs and reads outputs. A test with that key against an object outside the prefix receives `AccessDenied`.
- [x] The Hermes image contains a sync helper the run instructions invoke: download `input/` to the container work directory, and upload `result.json` and the candidate YAML to `output/` when the agent finishes. The helper is a fixed command, not a model-written script.
- [x] The candidate index the agent builds uses an `s3://` URI under `runs/<run_id>/candidate-index/`. `deepfix index` already accepts that URI and skips the local manifest for `s3://` (`src/deepfix_rag/index_manifest.py`). The API promotion path does not read that index. It keeps rebuilding locally from the candidate YAML, as `apply_candidate` does today.
- [x] Unit tests use a local S3 stand-in only if one is already in the test suite; otherwise they test the key allowlist and the object-key layout with the real helper against `tmp_path` and a recorded request plan. No mocks of the pipeline.

Verification note (2026-11-04), for the criteria above:

- `stage_run_bundle` is the API-side write that runs before `POST /v1/runs`. It is not called from the OpenHands path. The bucket is the bucket in `S3_URI`. The prefix is `optimization/runs/<run_id>/`. Objects are `input/champion.yaml`, `input/feedback_dev.jsonl`, `input/corpus/<relative path>` for each workspace corpus file, `input/ledger.jsonl` only when `runs/ledger.jsonl` exists, and `input/champion-index/` as a file copy of `indexes/current` (a symlink to a build is followed). The root key is what publishes that plan. `stage_run_bundle` is not wired into `start_optimization_run` (that stays US-004).
- The rewritten champion YAML sets `retrieval.lancedb_index_dir` to `/opt/data/workspace/runs/<run_id>/input/champion-index` and `data.path_or_repo` to `/opt/data/workspace/runs/<run_id>/input/corpus` with `source_type: directory`. Both include the run id, so a later run does not open the earlier LanceDB directory. `deepfix-sync download` writes `input/` under `/opt/data/workspace/runs/<run_id>/` when its work root is the default, which is where those paths point. The candidate index URI is `s3://<bucket>/optimization/runs/<run_id>/candidate-index`. `write_index_manifest` returns None for that URI. `apply_candidate` still calls `build_beside` against `indexes/current` and does not mention `candidate-index`.
- Holdout is not an input. The planner never reads a holdout file. A test lists every object written for a run and asserts the holdout query text and query id are absent. If those strings are present in any planned object, `stage_run_bundle` refuses to publish and writes nothing.
- The policy document is the one in Technical Considerations, unchanged. `provision_hermes_user` creates canned policy `hermes-optimizer-prefix`, creates user `hermes-optimizer`, and attaches that policy with `isGroup=false`. There is no `AssumeRole`. Compose gives the Hermes service `S3_ACCESS_KEY_ID` from `HERMES_S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` from `HERMES_S3_SECRET_ACCESS_KEY`, so LanceDB and `deepfix-sync` see the scoped key under the existing `S3_*` names. The root key is not interpolated into that service. Boot rejects `RUSTFS_ACCESS_KEY` and `RUSTFS_SECRET_KEY`, prefers `HERMES_S3_*` over a leaked `S3_ACCESS_KEY_ID`, and does not copy the S3 settings into the chat profile. `example.env` has empty placeholders for the two Hermes variables.
- This VM has no container runtime, so the prefix check did not run `rustfs/rustfs:latest`. It ran the official RustFS 1.0.4 linux/amd64 musl binary. The root key created `deepfix-index` and wrote `dev/not-in-prefix.txt`, `holdout/questions.jsonl`, and `optimization/other.txt`. The `hermes-optimizer` key put, got, listed, and deleted `optimization/runs/probe/output/result.json`, and the root key could read that object. Get, put, and delete of the outside objects, including the holdout key, returned `AccessDenied`. `ListBucket` with prefix `dev/` and `holdout/` also returned `AccessDenied`. The separate-bucket fallback was not used. US-009 still has to repeat the check on the deployed `latest` image.
- One docs gap: a signed admin call without `x-amz-content-sha256` is HTTP 400 `InvalidRequest`. The provisioner sends the header. The policy JSON itself was accepted.
- `deepfix-sync` is the fixed command (`download`, `upload`). It is the console script `deepfix_rag.hermes_sync:main`, and the image Dockerfile links it to `/usr/local/bin/deepfix-sync`. The image was not rebuilt in this session. Unit tests have no S3 stand-in: they publish and sync through a directory layout (`DEEPFIX_BUNDLE_ROOT` / `DirectoryObjectStore`) and assert the recorded request plan. No pipeline mocks.
- `uv run ruff check` and `uv run ruff format --check` passed. `uv run --env-file .env pytest -m "not network and not e2e"` was 233 passed, 23 deselected, and one failure: the known `tests/test_incremental_index.py::test_startup_resumes_indexing_and_deleting`. The RustFS check is `tests/test_rustfs_prefix_iam.py`, marked `network`, and it passed against the 1.0.4 binary.
- Not started: US-003 through US-010. The OpenHands child is still the optimization path.

### US-003: Hermes runs client in the API
**Description:** As a developer, I want one module that starts, polls, and stops a Hermes run, so the optimization thread does not speak HTTP from several places.

**Acceptance Criteria:**
- [ ] A domain module (for example `src/deepfix_rag/hermes_client.py`) is constructed with the base URL and `API_SERVER_KEY`. Route handlers do not call Hermes themselves.
- [ ] `start_run` sends `POST /v1/runs` with `Idempotency-Key` set to the DeepFix `run_id`, `provider: openrouter`, `model: anthropic/claude-haiku-5.5`, and the fixed optimization instructions. A replay (`202` and `Idempotency-Replayed: true`) returns the original Hermes `run_id`.
- [ ] `get_run` sends `GET /v1/runs/{run_id}` and returns the status string, `output` when present, and the error text when present. Statuses the client must distinguish: `started` or `running`, `stopping`, `completed`, `failed`, `cancelled`, `interrupted`, `unknown`.
- [ ] `stop_run` sends `POST /v1/runs/{run_id}/stop`.
- [ ] `iter_events` opens `GET /v1/runs/{run_id}/events` and yields each SSE event (`tool.started`, `tool.completed`, `tool.failed`, `message.delta`, `message.interim`, `run.completed`, `run.failed`, `run.cancelled`, `run.interrupted`) and each `: keepalive` comment. A dropped stream is retried. A retry does not by itself mark the run failed.
- [ ] On `unknown`, the client reads `admission_id` and `execution_generation` and sends `POST /v1/runs/{run_id}/resolve-unknown`, then treats the run as interrupted.
- [ ] The bearer token is sent only on the `Authorization` header. It is absent from logs, from the run prompt, and from exceptions returned to the admin UI.
- [ ] Tests run against a real local HTTP server started in the test process (not `unittest.mock`) and cover replay, `409 admission_conflict` / `idempotency_key_conflict`, `503` with `Retry-After`, `unknown` resolution, a stop body of `{"status": "stopping"}`, and an events stream that yields one `tool.started`, one `: keepalive`, and one `run.completed`.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-004: Optimization loop behind a flag
**Description:** As an operator, I want the current OpenHands path to stay the default until Hermes has passed, so a bad sandbox cannot block the nightly job.

**Acceptance Criteria:**
- [ ] `OPTIMIZATION_AGENT` is `openhands` (default) or `hermes`.
- [ ] `openhands` keeps today's `run_agent_job` path, including `fail_interrupted` on API startup (`ddab730`).
- [ ] `hermes` starts the US-003 client instead of the child process. `agent_available` is true when `GET /health` on the Hermes URL succeeds and `API_SERVER_KEY` is set. The admin status text when it is false is "Hermes is not set up on this server, so a run cannot start. The schedule and history are still shown."
- [ ] The prompt still requires `result.json` with `candidate_config_path`, `primary_metric`, `summary`, and `mlflow_run_id`. The API still ignores the agent's prose and still calls `finish_run` only from that file. With Hermes, the file is the object at `output/result.json`, downloaded into `runs/<run_id>/result.json` on the API workspace before `finish_run`.
- [ ] The prompt names the dev file, the champion config, and the run directory inside the Hermes work directory. It does not mention holdout, the product database, or the API-private directory.
- [ ] `candidate_config_path: null` still ends as `succeeded`. An invalid or missing file still ends as `failed`. Promotion rules in `src/deepfix_rag/promotion.py` stay unchanged.
- [ ] Existing optimization tests pass with the flag unset. New tests start a Hermes run through the lifecycle function with the local HTTP server from US-003 and a `result.json` the test server causes the downloader to write. They do not start the real agent. No mocks of eval or the pipeline.
- [ ] `uv run --env-file .env pytest -v -m "not network and not e2e"` passes.

### US-005: Cancel, timeout, and restart recovery
**Description:** As an admin, I want a run to end when I cancel it, when 3 hours pass, or when either container restarts, so a row is never stuck on `running`.

**Acceptance Criteria:**
- [ ] The run row stores `hermes_run_id` (new column, nullable). OpenHands rows leave it null.
- [ ] The background thread reads `GET /v1/runs/{hermes_run_id}/events` and, on a 15-second cadence, `GET /v1/runs/{hermes_run_id}`. It does not hold one chat request for the whole run. `completed` downloads the output and calls `finish_run`. `failed`, `cancelled`, and `interrupted` mark the row `failed` with the Hermes error text, or "Cancelled" / "Interrupted: Hermes restarted while the run was in progress" when the body has no error.
- [ ] Each events frame other than a keepalive is one structured step line on the API process logger (stderr, so it shows in `docker logs` of `backend-api`): `optimization run=<deepfix run_id> hermes_run=<id> event=<name> tool=<tool or empty> preview=<truncated>`. The same line is appended to the private step log from US-007. A status payload that stays `running` is not a step and is not a heartbeat.
- [ ] `OPTIMIZATION_HEARTBEAT_STALL_MINUTES` defaults to 5 and is read from the environment. A heartbeat is a non-keepalive events frame, or a fresh `output/heartbeat.json` from the `deepfix` shim in US-007. SSE `: keepalive` comments and a `running` status poll do not reset the stall clock: keepalives only prove the gateway is still sending frames during a long tool call. If the stall clock exceeds the configured window, the thread calls `stop_run` and marks the row `failed` with reason "Stalled: no heartbeat for 5 minutes." (the number is the configured window). A long `deepfix index` or `deepfix hpo` does not trip it, because the shim rewrites the heartbeat every 30 seconds while the command is still running.
- [ ] At `OPTIMIZATION_RUN_TIMEOUT_HOURS` (default 3) the thread calls `stop_run`, waits until the Hermes status is terminal or 2 minutes have passed, then marks the row `failed` with reason "Timed out" even if Hermes is still `stopping`.
- [ ] `POST /admin/optimization/runs/{run_id}/cancel` (admin only, `operation_id` `cancel_optimization_run`) calls the same stop path with reason "Cancelled". Members receive 403. A row that is not `running` receives 409. OpenHands rows still use the existing process-group kill.
- [ ] API startup, when the flag is `hermes`, does not mark a `running` row failed before it has asked Hermes. If Hermes reports the run still running and the row is younger than the timeout, the thread resumes polling. If Hermes reports a terminal status, the thread finishes the row as above. If Hermes is unreachable, the row stays `running` until `fail_stale_runs` (timeout plus the existing 15-minute grace) marks it `failed` with the existing stale reason. A second API process must not start a second Hermes run: the idempotency key is the DeepFix `run_id`.
- [ ] Hermes gateway shutdown already stores an in-flight run as `interrupted` (or `unknown` after an authority-owner restart). The poller and the startup pass both treat those as terminal for the DeepFix row. A test drives the local HTTP server through `running` → connection refused → `interrupted` and asserts the row ends `failed` and a later `POST /admin/optimization/run` is allowed. A second test keeps status at `running`, sends only `: keepalive` comments, and never updates `heartbeat.json`. The row ends `failed` with the stall reason before the 3-hour timeout. A third test updates `heartbeat.json` every minute for longer than the stall window and asserts the row stays `running`.
- [ ] `GET /admin/optimization` continues to call `fail_stale_runs`, so a dead poller thread cannot leave a row `running` past the grace window.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.

### US-006: Skills and the deepfix CLI inside the sandbox
**Description:** As the optimization agent, I want the same skill files and the same CLI the API was built with, so the procedure in `optimization-loop` still produces `result.json`.

**Acceptance Criteria:**
- [ ] `deepfix --help` inside the image lists `dataset`, `index`, `eval`, `mutate-config`, `diff-config`, `hpo`, `run-diagnostic`, `export`, `gate`, and `schema`.
- [ ] `GET /v1/skills` includes `optimization-loop`. If Hermes rejects the extra frontmatter on the current skill files (`failure_mode`, `trigger_conditions`, `cli_actions`), the image build fails the test and the fix is a documented frontmatter adjustment, not a rewritten procedure.
- [ ] The skill files in the image are mode `0444` and owned by root. A process as `hermes` cannot modify them. `skills.external_dirs` is the documented way Hermes loads an outside `SKILL.md` tree, and the docs state that a writable external directory can be edited by `skill_manage`.
- [ ] The optimization prompt tells the agent to follow `optimization-loop` and to use only `dense`, `hybrid`, and `bm25`. The budget line in that skill is updated from "15 agent steps" to the Hermes `agent.max_turns` value this image sets (starting point 40), because `skill_view` calls consume iterations. Exhausting `agent.max_turns` is a completed wrap-up (`finish_reason` `length` on chat, `hermes.error_code` `iteration_budget_exhausted`), not a cancel, and the API still requires `result.json`.
- [ ] `terminal.timeout` on the pinned image is greater than a sample `deepfix eval --retrieval-only` of the fixture corpus. The acceptance test records the configured value.
- [ ] Toolsets exposed on the `api_server` platform are terminal, file, and skills. Web, browser, delegation, cron, and messaging toolsets are off. Confirm the names with `GET /v1/toolsets` on the pinned image and record them in the image README comment beside the config.
- [ ] `deepfix hpo` and `deepfix run-diagnostic` stay on `PATH` because they are part of the CLI, and the skill still tells the agent not to call them during a round.

### US-007: Progress, traces, and logs
**Description:** As an operator, I want to see that a run is moving, so a hung optimization is not hours of silence in the API logs.

**Acceptance Criteria:**
- [ ] The production failure this story closes: the OpenHands child writes nothing to the `backend-api` container logs. Operators see `MLflow run created` and `POST /admin/optimization/run 200`, then no further line until the run times out. A Hermes run must write a step or heartbeat line to that same stderr log at least every `OPTIMIZATION_HEARTBEAT_STALL_MINUTES` while it is healthy.
- [ ] `iter_events` is required, not optional. The step line format is the one in US-005. Previews are truncated to 500 characters, matching the Hermes `tool.completed` preview cap, and passed through the same redaction the API already applies to secrets (FR-16). The lines are also appended to the API-private file `optimization-logs/<run_id>.log`, outside the workspace. That file is not copied to S3 and is not returned to members. The final `output` text, truncated to 64 KiB, is appended when the run ends.
- [ ] The command named `deepfix` on the Hermes `PATH` is a shim in front of the real CLI. While the real CLI is running, the shim writes `output/heartbeat.json` every 30 seconds (`{"run_id", "pid", "elapsed_s", "argv0", "written_at"}`) and writes one `deepfix-heartbeat` line to its own stderr on that same interval. Thirty seconds is several times shorter than the 5-minute stall window, so a long `deepfix index` or `deepfix hpo` keeps the file fresh. The shim forwards the child's stdout and stderr unchanged. `which deepfix` inside the container resolves to the shim. A direct call to the real interpreter does not have to heartbeat; the prompt and the skill tell the agent to run `deepfix`.
- [ ] Those stdout and stderr bytes are visible in `docker logs` of the Hermes container. Hermes already writes agent activity (API calls, tool dispatch, session lifecycle) to `$HERMES_HOME/logs/agent.log`, warnings to `errors.log`, and supervised gateway output to the container log (`docker logs` and `$HERMES_HOME/logs/gateways/<profile>/current`). The docs do not say that a terminal command's full stdout and stderr are copied onto `docker logs`. The image must mirror them there: if the pinned image already logs tool output into `agent.log`, the entrypoint follows that file onto the container stderr; otherwise the shim's own stderr is the stream s6 already forwards. US-009 checks that a line from `deepfix --help` appears in `docker logs hermes` during the run. Secrets stay redacted; Hermes documents that its log files redact secrets.
- [ ] `deepfix eval` still writes an MLflow run in `MLFLOW_DEV_RAG_EXPERIMENT` because the container has `MLFLOW_TRACKING_URI=http://mlflow:5000`. `mlflow_run_id` in `result.json` is still the admin-screen link.
- [ ] The same run also writes an MLflow trace for the Hermes agent turn and for each LLM call, tagged with the DeepFix `run_id`, in an experiment named `deepfix_hermes` (created if missing). The pages read do not document a native OTLP exporter. The implementation sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://mlflow:5000/v1/traces` and `OTEL_EXPORTER_OTLP_HEADERS=x-mlflow-experiment-id=<id>` when the pinned image honors them. If it does not, the image adds the smallest exporter that produces those spans. The unused `briancaffey/hermes-otel` plugin is allowed only when US-009 shows it is that exporter. Span attributes must not contain `API_SERVER_KEY`, `OPENROUTER_API_KEY`, or the S3 secret.
- [ ] A failed run's `reason` includes `Timed out`, `Stalled: no heartbeat for 5 minutes.`, the result-file error, or the Hermes error. It does not include those secrets.
- [ ] MLflow stays reachable the way Compose serves it today, including its published port. This story does not add MLflow authentication.

### US-008: Cancel control on the Optimization screen
**Description:** As an admin, I want a cancel button while a run is in progress, so I do not have to wait for the 3-hour timeout.

**Acceptance Criteria:**
- [ ] The Optimization screen shows "Cancel run" only while a run is `running`. The button calls `cancel_optimization_run`.
- [ ] After a successful cancel, the next poll shows the run `failed` with reason "Cancelled".
- [ ] Members who open the URL still see "This page is only available to admins." and no cancel button.
- [ ] The generated client is regenerated from `web/openapi.json`. No hand-written `fetch` to the new route.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-009: Dokploy end-to-end test
**Description:** As an operator, I want one real run on the Dokploy deployment before the default agent changes.

**Acceptance Criteria:**
- [ ] The deployment under test is the existing Dokploy stack plus the new internal Hermes service. Hermes has no public route.
- [ ] With `OPTIMIZATION_AGENT=hermes`, an admin starts a run against the sample corpus and a dev file of at least the skip threshold used for that environment (the test may lower the three minimum-question settings for the sample; it must not lower them in the default settings).
- [ ] The run reaches a terminal DeepFix status, `output/result.json` exists, and the API row is `succeeded`, `rejected`, `promoted`, or `failed` with a readable reason. It is not left `running`.
- [ ] Cancelling a second run that is inside `deepfix index` or `deepfix eval` moves the DeepFix row to `failed` / "Cancelled" within 2 minutes. Record whether the in-flight shell command actually stopped. That observation answers the open question on `POST /v1/runs/{id}/stop`.
- [ ] Restarting the Hermes container during a third run ends the DeepFix row as `failed` with the interrupted reason, once the API poller has run. Restarting the API container during a fourth run either resumes polling or, if Hermes has already finished, writes the matching terminal status. After both restarts, `SELECT count(*) FROM optimization_runs WHERE status = 'running'` is 0 or equals a run whose Hermes status is still `running` and whose age is under the timeout.
- [ ] MLflow shows the `deepfix eval` run id recorded in `result.json` when the agent completed an eval, and a trace in experiment `deepfix_hermes` whose spans include the agent turn and at least one LLM call, tagged with the DeepFix run id.
- [ ] During that run, `docker logs` of `backend-api` contains a step or heartbeat line after the `POST /admin/optimization/run 200` line, and `docker logs` of `hermes` contains a line of `deepfix` stdout or stderr. A run whose shim heartbeats are suppressed ends `failed` with the stall reason in under 10 minutes, not at 3 hours. A Hermes `GetObject` outside `optimization/runs/` returns `AccessDenied`.
- [ ] The OpenHands path still starts when the flag is flipped back to `openhands` for one run, or the test records that the OpenHands container was required for that path (see US-010).

### US-010: Remove the OpenHands path after Hermes is proven
**Description:** As an operator, I want the OpenHands child and the OpenHands container gone once the Hermes path is the one we run, so we do not maintain two sandboxes.

**Acceptance Criteria:**
- [ ] This story is not started until US-009 has passed on Dokploy.
- [ ] The default of `OPTIMIZATION_AGENT` becomes `hermes`.
- [ ] `scripts/openhands_runner.py`, the OpenHands virtualenv in the API image, `OPENHANDS_PYTHON`, and the `openhands` service in `compose.openhands.yml` are removed in the same change. `OH_SESSION_HOST` is removed with them. Compose currently sets that variable, and `scripts/openhands_runner.py` passes it as `Conversation(host=...)` and then drops the local workspace argument, so the service is on the OpenHands path whenever the variable is set. Confirm on Dokploy that an `openhands` run still completes after the service is stopped before deleting it, or delete the service and the child together.
- [ ] `compose.hermes.yml` as it exists on `dev` (published ports 9119 and 8642, dashboard basic-auth defaults, no `deepfix` binary) is not what this feature enables. The include stays commented until the US-001 service replaces that file.
- [ ] `tasks/notes-openhands-integration.md` is replaced with a short Hermes note: image digest, internal URL, required env vars, and the flag.
- [ ] `uv run --env-file .env pytest -v -m "not network and not e2e"` passes.

## Functional Requirements

- FR-1: The system must run Hermes in a Compose service separate from `backend-api`, on the Compose network only, with no published host port and no public domain.
- FR-2: The Hermes process must run as the image's non-root `hermes` user, with a memory limit and a CPU limit set on the service.
- FR-3: The Hermes image must provide the `deepfix` CLI from the same source revision as the API image, on Python 3.12, and the `.agents/skills/` tree mounted or copied read-only.
- FR-4: Hermes model calls must use OpenRouter (`OPENROUTER_API_KEY` in the Hermes environment, `model.provider: openrouter`, `model.default: anthropic/claude-haiku-5.5`). The product `LLM_API_KEY` must not be present in that environment.
- FR-5: The API must start an optimization run by `POST /v1/runs` and must store the returned Hermes run id. It must not use `POST /v1/chat/completions` as the 3-hour wait.
- FR-6: The API must follow `GET /v1/runs/{run_id}/events` and poll `GET /v1/runs/{run_id}` until the run is terminal, cancelled, stalled, timed out, or the Hermes status is `unknown` and has been resolved. A `running` status poll is not evidence of progress.
- FR-7: The API must cancel a run with `POST /v1/runs/{run_id}/stop` on admin cancel, on the 3-hour timeout, and when no heartbeat has arrived for `OPTIMIZATION_HEARTBEAT_STALL_MINUTES` (environment variable, default 5).
- FR-8: The API must be the only component that reads holdout, scores holdout, and writes the champion config. Hermes must not receive holdout bytes in the prompt, the run bundle, or the environment.
- FR-9: The API must keep the current skip rules: `OPTIMIZATION_MIN_DEV_QUESTIONS` (50), `OPTIMIZATION_MIN_NEW_DEV_QUESTIONS` (10), `OPTIMIZATION_MIN_HOLDOUT_QUESTIONS` (15), `OPTIMIZATION_MIN_PRIMARY_DELTA` (0.025), `OPTIMIZATION_SECONDARY_TOLERANCE` (-0.01), and `OPTIMIZATION_RUN_TIMEOUT_HOURS` (3).
- FR-10: The dev/holdout split must stay `int(sha256(normalized query), 16) % 100 < 20` in `feedback.is_holdout`.
- FR-11: Hermes must open LanceDB with `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, and an `s3://` URI, the same variables `RAGWorkflow._get_vector_store` already reads. No volume of the API workspace or of RustFS data may be mounted into Hermes.
- FR-12: The Hermes S3 credential must be a RustFS IAM user key, not the root key, with the prefix policy in Technical Considerations. Object access is `optimization/runs/*` on bucket `deepfix-index`. The API root key writes the run input, including the champion-index snapshot, and reads the output.
- FR-13: A restart of the API or of Hermes must end with no DeepFix row stuck in `running` after the poller has observed a terminal Hermes status, or after the existing stale grace if Hermes cannot be reached.
- FR-14: `OPTIMIZATION_AGENT=openhands` must remain the default until US-009 passes. The OpenHands child must keep working while the flag is `openhands`.
- FR-15: The browser must talk only to the DeepFix API. A future admin chat uses the separate `chat` Hermes profile (port 8643, its own bearer key, no terminal), and the API proxies that profile. The chat session is never an optimization run session. The chat contract in Design Considerations is the boundary: it may read status, explain results, and start or cancel a run through the API, and it must not promote or receive holdout questions.
- FR-16: Secrets (`API_SERVER_KEY`, `OPENROUTER_API_KEY`, the Hermes S3 secret, the product database path) must not appear in prompts, in `result.json`, in MLflow params or span attributes, in container logs, or in the admin run reason.
- FR-17: Every Hermes optimization run must write structured step lines, from `GET /v1/runs/{run_id}/events`, to the API process stderr and to `optimization-logs/<run_id>.log`.
- FR-18: The `deepfix` shim must refresh `output/heartbeat.json` every 30 seconds while a CLI command runs, including `index` and `hpo`. The API must fail the row with "Stalled: no heartbeat for 5 minutes." when that file and the non-keepalive events are both quiet for `OPTIMIZATION_HEARTBEAT_STALL_MINUTES` (default 5). SSE keepalives do not count.
- FR-19: The child's stdout and stderr must appear in `docker logs` of the Hermes container. The Hermes agent turn and each LLM call must be an MLflow trace in experiment `deepfix_hermes`, tagged with the DeepFix run id. `deepfix eval` must still create its MLflow run.

## Non-Goals

- Building the natural-language admin chat UI in this feature. The sandbox must still leave a second Hermes profile for that chat, with the contract in Design Considerations. The chat is not built here.
- Letting Hermes promote a config, score holdout, or write `configs/champion.yaml` and `indexes/current`.
- Moving the serving index off the API volume. The API copies a snapshot into the run prefix for Hermes. Search and Ask keep reading the local serving index.
- Replacing the in-process cron scheduler with Hermes `/api/jobs`.
- Enabling the Hermes dashboard, the webapp, or any published port.
- Adding authentication to the public MLflow server.
- Changing the promotion thresholds, the sha256 holdout split, or the allowed backends (`dense`, `hybrid`, `bm25`).
- Implementing `deepfix export` (it still exits 2 with "export is not implemented yet") or turning `deepfix hpo` on inside a round.
- Treating the unused `briancaffey/hermes-otel` plugin as the plan. It is allowed only when it is the exporter that actually writes the `deepfix_hermes` spans.
- A shared Docker volume between `backend-api` and Hermes.

## Design Considerations

The Optimization screen stays the one in the product UI. This feature adds one control, "Cancel run", visible to admins while a row is `running`. The agent-unavailable sentence changes with the flag, as in US-004. No new member-facing screen.

### Admin chat profile

The chat UI is a later feature. The sandbox that this plan deploys must already keep a second Hermes profile, `chat`, isolated from the optimization profile. Hermes profiles are separate homes (config, credentials, sessions, skills) inside one container. The chat profile gets its own `API_SERVER_PORT` (8643, not published) and its own `API_SERVER_KEY`. The optimization profile stays on 8642. A chat session id is never the optimization run's session id.

The chat profile has no terminal toolset and no file-write toolset. Both profiles share one container user, and a terminal on the chat profile could read the optimization profile's `.env` on the shared data volume. Leaving the terminal off is the isolation. The chat profile also does not receive `HERMES_S3_ACCESS_KEY_ID`, the optimization OpenRouter key, or the root RustFS key.

What the chat profile may do, only by asking the DeepFix API (the API performs the call in the signed-in admin's session; the profile does not hold a standing admin password):

- Read optimization status: schedule, champion diff, and recent runs (status, reason, primary metric, dev delta, the aggregate holdout delta, wins, and losses already on `GET /admin/optimization`).
- Explain those results in conversation.
- Propose a candidate in the conversation. The proposal is text. It does not write `configs/champion.yaml`.
- Start a run with `POST /admin/optimization/run`, and cancel one with `POST /admin/optimization/runs/{run_id}/cancel`. Starting a run still goes through `start_optimization_run`, including the skip rules and the holdout count check. The chat does not upload the dev file itself.

What it must not do:

- Promote a config, write the champion file, or switch the serving index. There is no promote route for it to call. Promotion stays inside `apply_candidate`.
- See holdout questions, holdout document ids, or the holdout JSONL. Aggregate counts and deltas that the admin screen already shows are the only holdout fields it may hear. The API must not add a route that returns holdout rows to this profile.
- Read or write `optimization/runs/` in RustFS, run `deepfix`, or attach to the optimization profile's sessions.
- Use the optimization profile's approval mode (`off`) or its `deepfix` shim. Those exist only on the optimization profile.

## Technical Considerations

### What the code does today

- `start_optimization_run` in `src/deepfix_rag/optimization.py` exports feedback, skips when dev, new dev, or holdout counts are below the settings, and otherwise inserts a `running` row and starts a daemon thread.
- The thread calls `run_agent_job`, which writes `runs/<run_id>/job.json` and runs `scripts/openhands_runner.py` with the workspace as cwd. The child environment allowlist is `DEEPFIX_LLM_*`, MLflow, embeddings, and Cohere. `S3_*`, `LLM_API_KEY`, and `HF_TOKEN` are not passed. The default wait is 3 hours, then `SIGKILL` on the process group.
- `finish_run` trusts only `result.json` (`AgentResultFile`). `apply_candidate` re-scores dev from the snapshot file and holdout from memory, then promotes or rejects.
- Holdout is `is_holdout` in `src/deepfix_rag/feedback.py`: SHA-256 of the normalized query, interpreted as an integer, modulo 100, less than 20.
- API startup calls `fail_interrupted` (`src/deepfix_rag/api/app.py`), which marks leftover `running` rows failed ("Interrupted: backend restarted while the run was in progress") and signals the recorded pid. `fail_stale_runs` marks rows older than the timeout plus 15 minutes failed. `GET /admin/optimization` calls `fail_stale_runs`. That behavior is `ddab730`.
- `RAGWorkflow._get_vector_store` connects LanceDB with `aws_access_key_id`, `aws_secret_access_key`, `region`, and `endpoint` from `S3_*` when `lancedb_index_dir` starts with `s3://`. The product workspace currently forces a local directory (`bind_locations` / `documents.py`). `deepfix index --index-dir` is how the agent overrides it. Remote URIs skip the on-disk manifest.
- Compose (`docker-compose.yml`) runs `rustfs` (ports 9000 and 9001), `mlflow` (port 5000, `MLFLOW_SERVER_ALLOWED_HOSTS` includes the public MLflow host), `diagnostic-agent`, `backend-api`, `web`, and, through includes, graph databases and `openhands`. The `hermes` include is commented out. `compose.hermes.yml` publishes 8642 and 9119, turns the dashboard on, and does not install `deepfix`.
- `LLM_MODEL` defaults to `anthropic/claude-haiku-5.5`. `resolve_litellm_model` prefixes `openrouter/` when the base URL host ends with `openrouter.ai`. `DEEPFIX_LLM_DROP_PARAMS` sets `litellm.drop_params` because that model rejects a temperature other than 1. The OpenHands child uses `DEEPFIX_LLM_MODEL_NAME` (default `openai/gpt-6-luna-pro`) and adds the same prefix in the runner. Hermes must not copy that prefix: its OpenRouter provider takes the OpenRouter id.
- CLI commands registered in `src/deepfix_rag/main.py`: `dataset` (`load`, `list`, `generate-unanswerable`), `eval`, `index`, `run-diagnostic`, `mutate-config`, `diff-config`, `gate`, `schema`, `export` (unimplemented), `hpo`.
- The optimization skill (`.agents/skills/optimization-loop/SKILL.md`) is the procedure: one hypothesis, retrieval-only, `result.json`, no promotion. Pathology skills are siblings. `plugins/deepfix-rag/plugin.yaml` points at that directory and declares Hermes compatibility.

### Verified Hermes facts

Checked against the docs on 2026-11-04. These are the facts the design relies on.

| Fact | Source |
| :--- | :--- |
| Hermes Agent is the Nous Research project at `github.com/NousResearch/hermes-agent`. The API server is an OpenAI-compatible HTTP service started with the gateway when `API_SERVER_ENABLED=true`. Default listen is `http://127.0.0.1:8642`. Binding beyond loopback also needs `API_SERVER_HOST=0.0.0.0` and `API_SERVER_KEY` of at least 8 characters. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker) |
| Auth is `Authorization: Bearer <API_SERVER_KEY>` on every deployment, including loopback. `GET /v1/capabilities` reports `auth.type` `bearer` and `auth.required` true. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| `POST /v1/chat/completions` is the OpenAI chat surface. The documented contract is stateless: the client sends `messages`. `"stream": true` is SSE (`chat.completion.chunk`) plus `event: hermes.tool.progress`. A bare `model` without `provider` is ignored unless `gateway.platforms.api_server.direct_model_requests` is true. An explicit `provider` is honored. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| `POST /v1/responses` stores conversation state. `previous_response_id` or `conversation` chains turns, including tool calls the server already executed. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| `POST /v1/runs` returns a `run_id` (programmatic guide: HTTP 202). `GET /v1/runs/{run_id}` polls. `GET /v1/runs/{run_id}/events` is SSE. `POST /v1/runs/{run_id}/stop` returns `{"status": "stopping"}` immediately and the run later settles as `cancelled`. Stop is at the next safe interruption point; the run stays `stopping` until the executor exits. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [Programmatic integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration) |
| An `Idempotency-Key` on `POST /v1/runs` is reserved before work, kept 24 hours, and an exact retry returns the same `run_id` with `Idempotency-Replayed: true`, including after a gateway restart and after a terminal run. A different payload with the same key is HTTP 409 `idempotency_key_conflict`. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| Gateway shutdown persists an active run as `interrupted` with error `Gateway shutdown interrupted the run.` before asking the agent to stop, so a durable run does not survive a restart as `running`. A canonical run whose owner restarted mid-turn is `unknown` (not terminal) until `POST /v1/runs/{run_id}/resolve-unknown` with `admission_id` and `execution_generation`. Ordinary stop on an unknown run returns 409 `unknown_execution`. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| Chat completions report `finish_reason` `cancelled` for an interrupted turn and `error` for a failure. Hitting `agent.max_turns` still returns the wrap-up, with `finish_reason` `length` and `hermes.error_code` `iteration_budget_exhausted` on chat, and `status` `incomplete` on Responses. That is not a cancellation. `HERMES_MAX_ITERATIONS` defaults to 500. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [Environment variables](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| All of those SSE streams emit a `: keepalive` comment when no event has been sent for 10 seconds, so a long tool call does not look like a dead HTTP connection. `tool.started` carries the tool name and an argument preview. `tool.completed` carries duration, an error flag, and a result preview truncated to 500 characters after secret redaction. The final answer arrives on `run.completed`. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| Supervised `gateway run` sends gateway output to `docker logs`. Per-profile gateway files live at `$HERMES_HOME/logs/gateways/<profile>/current`. `agent.log` records API calls, tool dispatch, and session lifecycle. `errors.log` records warnings and errors. The configuration guide says log files redact secrets. `hermes logs` reads those files. | [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker), [CLI commands](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Sessions live at `/api/sessions`: list, create, get, patch, delete, messages, fork, synchronous `chat`, and `chat/stream`. `GET /v1/skills` and `GET /v1/toolsets` list skills and toolsets. `GET /health` is unauthenticated liveness (`{"status": "ok"}`). `GET /health/detailed` is an authenticated readiness check. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| `/api/jobs` is CRUD for scheduled jobs (the cron surface), plus pause, resume, and `POST /api/jobs/{job_id}/run`. It is not the status API for an ad-hoc run. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| File parts (`file`, `input_file`, `file_id`) and non-image `data:` URLs return `400 unsupported_content_type`. The corpus cannot be uploaded as a chat attachment. | [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) |
| Client-supplied OpenAI `tools` are not a supported way to add tools. The issue tracker records that the API server owns its toolset and does not forward a request `tools` array to the model. | [Issue 28825](https://github.com/NousResearch/hermes-agent/issues/28825) |
| Skills are `SKILL.md` documents, compatible with the agentskills.io standard, loaded in three steps: a name/description index, then `skill_view(name)`, then a single reference file. External directories are `skills.external_dirs` in `config.yaml`. The docs' example is `~/.agents/skills`. A writable external directory can be modified by the agent. Same-name precedence is project, then `~/.hermes/skills/`, then `skills.create_dir`, then `external_dirs`. | [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) |
| OpenRouter is configured with `OPENROUTER_API_KEY` in `~/.hermes/.env` and `model.provider: openrouter` plus `model.default` in `config.yaml`. The model id is the OpenRouter id (`anthropic/claude-sonnet-latest` in the cookbook), not a LiteLLM `openrouter/` prefix. Optional `provider_routing` selects OpenRouter upstreams. | [Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers), [OpenRouter cookbook](https://openrouter.ai/docs/cookbook/coding-agents/hermes-integration) |
| The official image is `nousresearch/hermes-agent`, tags `latest`, `stable`, `main`, and `X.Y.Z`, with a digest recommended for a pin. State lives in `/opt/data` (`.env`, `config.yaml`, `skills/`, `sessions/`, `logs/`). `/opt/hermes` is root-owned and read-only to the runtime `hermes` user. The image Python is 3.14. `gateway run` is supervised by s6. `API_SERVER_ENABLED` is required for the API port. | [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker) |
| Terminal backends include `local` (commands run in the Hermes process environment) and `docker` (a nested sandbox). Documented `terminal.timeout` is 180 seconds per command. `approvals.mode` is `smart` (default), `manual`, or `off`. `off` matches `HERMES_YOLO_MODE`. `approvals.deny` still blocks matching commands when mode is `off`. Unanswered approval prompts fail closed (default 300 seconds). | [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration), [Security](https://hermes-agent.nousresearch.com/docs/user-guide/security) |
| Provider HTTP timeouts default to `HERMES_API_TIMEOUT` 1800 seconds and a stale-call detector of 90 seconds, overridable with `providers.<id>.request_timeout_seconds` and `stale_timeout_seconds`. | [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| The dashboard binds 9119 inside the image when `HERMES_DASHBOARD=1`. A non-loopback bind refuses to start without an auth provider. `HERMES_DASHBOARD_INSECURE` no longer disables auth. The project documents a June 2026 incident in which exposed dashboards and API servers were used to plant a backdoor. | [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker) |
| Two Hermes gateways must not share one data directory. A second profile in the same container gets its own home and, for the API server, its own `API_SERVER_PORT` in that profile's `.env`. | [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker) |

### Verified RustFS facts

`docker-compose.yml` runs `image: rustfs/rustfs:latest`. That tag is floating. Nothing in this plan executed that image. The findings below are from the current RustFS docs and from GitHub issues, read on 2026-11-04.

| Fact | Source |
| :--- | :--- |
| RustFS has built-in IAM: root credentials (`RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`), IAM users, groups, service accounts, and STS. Root bypasses IAM identity policies. Applications are supposed to use IAM users or service accounts, not root. | [IAM overview](https://docs.rustfs.com/en/security-compliance/iam), [S3 protocol](https://docs.rustfs.com/en/administration/protocols/s3) |
| A policy document is IAM-style JSON (`Version` `2012-10-17`). Resource ARNs are `arn:aws:s3:::bucket` and `arn:aws:s3:::bucket/prefix/*`. Condition operators include `StringLike`. | [Users, groups, and policies](https://docs.rustfs.com/en/security-compliance/iam/policies) |
| The docs' own example "Read/Write Restricted to a Prefix" allows `s3:ListBucket` on the bucket only when `s3:prefix` is `StringLike` `tenant-42/*`, and allows `s3:GetObject` and `s3:PutObject` only on `arn:aws:s3:::app-data/tenant-42/*`. Bucket policies can also limit an object ARN to a prefix (`arn:aws:s3:::my-bucket/public/*`). | [Users, groups, and policies](https://docs.rustfs.com/en/security-compliance/iam/policies), [Bucket policy](https://docs.rustfs.com/en/administration/data/bucket/policy) |
| A maintainer answered a prefix-policy question with the same shape: `ListBucket` plus a `StringLike` `s3:prefix` condition, and `GetObject` on `arn:aws:s3:::bucket/prefix/`. | [rustfs/rustfs#1399](https://github.com/rustfs/rustfs/issues/1399) |
| Service accounts are derived keys. An embedded policy is an extra restriction (parent and session policy must both allow). They cannot call `AssumeRole`. | [Service accounts and STS](https://docs.rustfs.com/en/security-compliance/iam/sts) |
| STS `AssumeRole` with a split session policy (object ARN on one statement, `ListBucket` plus `s3:prefix` on another) returned 403 for `ListBucket` through at least `v1.0.0-alpha.99`. | [rustfs/rustfs#2699](https://github.com/rustfs/rustfs/issues/2699) |

The design uses a prefix-scoped IAM user, not a second bucket. The policy attached directly to user `hermes-optimizer` is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::deepfix-index"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::deepfix-index"],
      "Condition": {"StringLike": {"s3:prefix": ["optimization/runs/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::deepfix-index/optimization/runs/*"]
    }
  ]
}
```

Attach that document to the IAM user. Do not issue the Hermes credential with `AssumeRole` and a session policy until issue #2699 is confirmed fixed on the image Dokploy is running. The root key (`S3_ACCESS_KEY_ID`, mapped to `RUSTFS_ACCESS_KEY` in Compose) writes the run bundle and reads outputs. Hermes never receives it.

Dokploy provisioning is a one-time manual step, not a boot script inside the Hermes container (that would put the root key in the sandbox). An operator who already has the root key in Dokploy creates the user and the policy with `rc` or the RustFS console against the internal endpoint, then stores the new access key and secret as Dokploy secrets `HERMES_S3_ACCESS_KEY_ID` and `HERMES_S3_SECRET_ACCESS_KEY` on the Hermes service only. Repeat the step only to rotate the key. US-009 confirms a `GetObject` outside `optimization/runs/` is `AccessDenied` on the deployed `latest` image. If it is not, switch to a bucket `deepfix-hermes` and a key that cannot read `deepfix-index`, and have the root key copy the champion index into that bucket.

### Assumptions

These are not in the sources above. Implementation must not treat them as guarantees.

- `POST /v1/runs/{id}/stop` interrupts the agent between tool calls. The docs do not say it kills a shell command that is still inside `terminal.timeout`. US-009 records what the pinned image actually does.
- The prefix policy below is what the current RustFS docs specify. `docker-compose.yml` pulls `rustfs/rustfs:latest`, a floating tag, so the deployed image was not executed in this plan. US-009 checks `AccessDenied` outside the prefix on that image. If that check fails, the fallback is a separate bucket `deepfix-hermes` whose key is the only one in the Hermes container, plus a read-only copy of the champion index written into that bucket by the root key. Do not switch to that fallback before the check fails.
- `agent.max_turns: 40` is enough for one skill load plus one hypothesis. The numeric default of the `agent.max_turns` config key was not on the pages read; the env default of `HERMES_MAX_ITERATIONS` is 500.
- 4 GiB and 2 CPUs are enough for a sample index and a retrieval-only eval. The Dokploy run is what raises them.
- The extra YAML keys on our `SKILL.md` files are ignored by Hermes's loader. US-006 checks `GET /v1/skills`.
- Copying the serving LanceDB directory to the run prefix is an acceptable start-up cost for the pilot corpus. The copy size was not measured.
- The pages read do not document a native OTLP exporter, and they do not say that terminal stdout and stderr are copied into `docker logs` or `agent.log`. US-007 requires both, and US-009 checks them. `tool.completed` is documented at the end of the tool, not as a live stdout stream, which is why the shim heartbeat is a file the API can read during a long `deepfix index`.
- The stall window default is the owner's choice: 5 minutes, via `OPTIMIZATION_HEARTBEAT_STALL_MINUTES`. The shim interval of 30 seconds is the plan's choice inside the 30–60 second range the owner allowed.
- The `v2026.6.19` tag in the unused `compose.hermes.yml` is older than the runs API described here. The pin is a digest chosen after `GET /v1/capabilities`, not that tag.

### Long-run protocol

```
Admin or cron
    -> start_optimization_run (skip rules, holdout counted, holdout not written)
    -> upload run bundle to s3://deepfix-index/optimization/runs/<run_id>/input/
    -> POST /v1/runs  (Idempotency-Key = deepfix run_id)
    -> store hermes_run_id
    subscribe GET /v1/runs/{hermes_run_id}/events
    loop:
        event other than keepalive -> step line on API stderr and optimization-logs/<run_id>.log
        heartbeat.json younger than OPTIMIZATION_HEARTBEAT_STALL_MINUTES (default 5), or a non-keepalive event -> reset stall clock
        keepalive or status=running -> do not reset the stall clock
        completed -> download output/result.json -> finish_run (API scores holdout)
        failed | cancelled | interrupted | resolved unknown -> mark failed
        no heartbeat for 5 min -> POST .../stop -> mark Stalled
        now - started_at > 3h -> POST .../stop -> mark Timed out
        deepfix shim -> stdout/stderr on the Hermes container log, heartbeat.json every 30s
Admin Cancel -> POST .../stop -> mark Cancelled
API restart -> GET the stored hermes_run_id and resume or finish; do not insert a second run
Hermes restart -> next poll sees interrupted or unknown -> mark failed
```

Chat completions on the `chat` profile stay available for a later admin chat. They are not the optimization wait, and they are not the optimization profile. `/api/jobs` stays unused so Hermes cron cannot schedule a second optimizer.

The OpenHands goal judge is not reimplemented. The API already accepts or rejects the run from `result.json` alone.

### Security

- Network: Hermes is attached to the Compose network. Compose does not publish 8642 or 9119. No Dokploy domain points at it. The dashboard and the webapp are off.
- API key: `API_SERVER_KEY` is a generated secret of at least 32 hex characters (the docs' minimum is 8). It lives in the API environment and the Hermes environment. The browser never sees it. The future chat proxies through the API.
- Model key: `OPENROUTER_API_KEY` is a key distinct from `LLM_API_KEY`, stored only in the Hermes environment (`~/.hermes/.env` on the Hermes volume). The backend does not send it in the run body.
- S3: Hermes's key is a RustFS IAM user limited to `optimization/runs/*`, provisioned once as below. The root key stays on the API. The API copies the champion index into the run prefix. The chat profile does not get either key.
- Tools: `terminal.backend: local` so commands run as `hermes` inside this container. No Docker socket, so the agent cannot start a sibling container. `approvals.mode: off` is acceptable only because the container is the sandbox. `approvals.deny` covers pipe-to-shell and force-push. The skill files are not writable.
- Holdout and promotion: unchanged. The prompt and the S3 listing test enforce the boundary.
- MLflow: Compose publishes port 5000 and allows the public host `deepfix-mlflow.mittelstand-zukunftslab.de`. This feature leaves that in place. Eval artifacts and the API-private Hermes log must not contain the bearer token, the OpenRouter key, or the S3 secret. The skill already forbids printing API keys.
- Egress: the terminal can open outbound connections. The intended destinations are OpenRouter, `rustfs:9000`, and `mlflow:5000`. A platform egress allowlist for those hosts is part of the Dokploy test if the platform supports it. Without it, a prompt injection can try to send the Hermes secrets out. The scoped keys limit the damage. This is called out in Open Questions rather than claimed as solved.

### Observability

Production today is silent. The OpenHands child writes its transcript to `runs/<run_id>/agent.log` inside the workspace and does not write those lines to the `backend-api` container log. An operator watching that log sees `MLflow run created` and `POST /admin/optimization/run 200`, then nothing until the timeout. A status of `running` for those hours does not say whether the agent is in `deepfix eval` or wedged. This design uses four channels:

1. **Job API steps.** The API subscribes to `GET /v1/runs/{id}/events` and logs every non-keepalive frame (`tool.started`, `tool.completed`, `tool.failed`, message deltas, terminal run events) as one structured line on the API process stderr. The same line is stored in `optimization-logs/<run_id>.log` on the API-private volume. `GET /v1/runs/{id}` is still how the thread learns the terminal status. A body that stays `running` is not logged as progress. The admin browser does not open the SSE stream; a later chat can.
2. **Heartbeats and stall detection.** The `deepfix` shim rewrites `output/heartbeat.json` every 30 seconds while a CLI command is in progress, including when that command prints nothing. `index` and `hpo` stay inside the window because the file keeps moving. The stall clock resets on that file or on a non-keepalive event. It does not reset on `: keepalive` (every 10 seconds while a tool is quiet) or on a `running` poll. The OpenHands failure mode is the second of those: the row stays `running` and the API log stays quiet. A Hermes keepalive is the same kind of non-progress, so it must not count. After `OPTIMIZATION_HEARTBEAT_STALL_MINUTES` (default 5) with neither heartbeat, the API stops the Hermes run and marks the row `failed` with "Stalled: no heartbeat for 5 minutes." The 3-hour timeout remains the outer bound. `fail_stale_runs` still uses its existing 15-minute grace after the 3-hour timeout. That grace is the restart backstop from `ddab730`. It is not this stall window.
3. **Hermes container logs.** `deepfix` stdout and stderr must show up in `docker logs` of the Hermes container, so an operator can read the CLI's own JSON and errors without the API. Hermes already keeps `agent.log` (API calls, tool dispatch, session lifecycle), `errors.log`, and a per-profile gateway log, and supervised gateway output is what `docker logs` shows. Full terminal stdout is not documented as part of that stream, so the image has to mirror it. Log files redact secrets; the shim must not print keys either.
4. **MLflow.** `deepfix eval` keeps writing a run in `MLFLOW_DEV_RAG_EXPERIMENT`, and the admin link stays `mlflow_run_id`. Separately, the Hermes turn and each LLM call are spans in experiment `deepfix_hermes`, tagged with the DeepFix run id, sent to `http://mlflow:5000/v1/traces`. MLflow stays public. Spans and logs carry no secrets.

`GET /health/detailed` remains the in-network readiness check. It is authenticated and stays unpublished. A dropped events connection is retried and is not, by itself, a finished run. The stall clock is what ends a stream that never comes back.

### Test plan

- Unit and integration, local: `uv run --env-file .env pytest -v -m "not network and not e2e"`. Covers the client, the events parser, the flag, the S3 key layout, holdout absence, timeout, cancel, stall (keepalives only versus a fresh heartbeat file), and both restart sequences, using a real local HTTP server and `tmp_path`. No `unittest.mock` of domain code, matching `AGENTS.md`.
- Image smoke, local Compose: `GET /health`, `GET /v1/capabilities`, `deepfix --help`, `GET /v1/skills` contains `optimization-loop`, a one-turn `POST /v1/runs` whose prompt is `deepfix --help` returns that help text in `output`, and that help text also appears in `docker logs` of the Hermes container.
- Dokploy end-to-end: US-009. That run is the gate for changing the default flag and for deleting OpenHands.

### Phased milestones

| Phase | Stories | Done when |
| :--- | :--- | :--- |
| 1. Sandbox image | US-001 | Internal health check and capabilities check pass. No host port is published. |
| 2. Bundle and client, flag default `openhands` | US-002, US-003, US-006 | A local HTTP test plus a local Compose one-turn run can see `deepfix` and the skill. The nightly job still uses OpenHands. |
| 3. Lifecycle | US-004, US-005, US-008 | A flagged run follows events, cancels, stalls, times out, and survives an API restart without a second Hermes run. The cancel button works in the browser. The API log shows a step line. |
| 4. Evidence | US-007 | A flagged eval's MLflow run id appears on the admin screen, `deepfix_hermes` has agent and LLM spans, heartbeat.json moves during a long command, and `docker logs hermes` shows CLI output. |
| 5. Prove it | US-009 | The Dokploy script in US-009 passes, including both restarts. |
| 6. Cut over | US-010 | Default is `hermes`. The OpenHands child, its venv, and the `openhands` service are gone. |

## Success Metrics

- A Hermes run on Dokploy reaches a terminal DeepFix status, and the row is not `running` afterwards.
- While that run is healthy, `docker logs` of `backend-api` gains a step or heartbeat line at least every 5 minutes after `POST /admin/optimization/run 200`, and `docker logs` of `hermes` shows `deepfix` stdout or stderr.
- A run with the shim heartbeat suppressed ends `failed` with the stall reason in under 10 minutes. A run whose `heartbeat.json` is rewritten every 30 seconds is not stalled at 5 minutes.
- MLflow experiment `deepfix_hermes` contains a trace for that run with an agent span and an LLM span.
- Restarting either container during a run leaves zero `running` rows that Hermes no longer reports as running, once the poller or the stale grace has fired.
- The S3 listing for the run contains no holdout query text.
- With the flag left at `openhands`, the existing optimization tests pass and a Dokploy OpenHands run still completes until US-010.
- After US-010, one nightly or manual run still produces a `result.json` the API can accept or reject, and Search and Ask keep using the champion the API promoted.
- No request from the browser is sent to port 8642. The Hermes service has no public route.

## Open Questions

- Does `POST /v1/runs/{id}/stop` on the pinned image kill an in-flight `deepfix` process, or only the agent loop after the terminal tool returns? US-009 records the answer. If the shell keeps running until `terminal.timeout`, the 2-minute cancel bound fails and the image needs a further control (shorter timeout is not acceptable for a real index).
- Which exporter on the pinned image writes the `deepfix_hermes` spans to MLflow? Native OTLP headers are the first attempt. The `hermes-otel` plugin is the fallback only if a trial shows the spans. The requirement is the spans, not a specific plugin.
- Which digest implements the runs surface above? `compose.hermes.yml` pins `v2026.6.19`, which this plan does not assume is current.
- What is the effective per-command ceiling on that image (`terminal.timeout` versus any `timeouts.tools` cap in the example config)? A 180-second default would cut off `deepfix index` and `deepfix eval`.
- Does Hermes send a temperature other than 1 when calling `anthropic/claude-haiku-5.5` through OpenRouter? This repo sets `litellm.drop_params` because that model rejects other temperatures. Hermes is not LiteLLM, and the matching Hermes setting was not found.
- How large is the serving-index copy into the run prefix on the pilot corpus, and does it fit the 4 GiB limit together with the embedder?
- Can Dokploy restrict the Hermes container's egress to OpenRouter, RustFS, and MLflow? If it cannot, secret exfiltration through the terminal tool remains a residual risk.

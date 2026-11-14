# CHAT-001 — Start the chat profile as a gated standalone gateway

## Type
Feature

## Status
Draft

## Summary
An operator can turn admin chat on with one setting, and then the `chat` Hermes profile answers on
`http://hermes:8643` inside the Compose network with its own bearer key and its own model resolution. With the
setting off — the default — no chat gateway process runs and nothing listens on 8643, so a deployment that does
not want chat pays nothing for it.

## PRD traceability
- Related: FR-001, FR-002, FR-020, FR-029
- Decisions: D-1 (off by default, standalone gateway, no memory when off), D-3 (`HERMES_CHAT_MODEL` falls back
  to `HERMES_MODEL`)
- Journeys: none directly; this story is the precondition for J1–J4
- Supersedes: the "not started" description of the chat profile in `tasks/prd-hermes-optimizer.md`
  "Design Considerations → Admin chat profile" (see that PRD's amendment list, item 1). The amendment text in
  `tasks/prd-hermes-optimizer.md` is written in CHAT-011, not here.

## Dependencies
- Requires: nothing in this PRD. Depends on `tasks/prd-hermes-optimizer.md` US-001…US-010 being in the tree
  (`docker/hermes/sandbox_boot.py` with `CHAT_PROFILE`, `CHAT_API_PORT`, `CHAT_API_TOOLSETS`; `compose.hermes.yml`
  with the `hermes` service).
- Blocks: CHAT-002 onward (every later story assumes the profile's contract and the settings exist).

## Acceptance criteria

Settings and plumbing:

1. Given a clean environment, when `Settings()` is built, then `HERMES_CHAT_ENABLED` is `False`,
   `HERMES_CHAT_BASE_URL` is `"http://hermes:8643"`, and the eleven `ADMIN_CHAT_*` settings exist with the PRD
   defaults: `ADMIN_CHAT_MAX_PROMPT_CHARS=48000`, `ADMIN_CHAT_MAX_MESSAGES=60`,
   `ADMIN_CHAT_TURN_TIMEOUT_SECONDS=300`, `ADMIN_CHAT_DIGEST_MAX_CHARS=6000`,
   `ADMIN_CHAT_MAX_TURNS_PER_WINDOW=30`, `ADMIN_CHAT_TURN_WINDOW_SECONDS=300`,
   `ADMIN_CHAT_MAX_CONCURRENT_TURNS=2`, `ADMIN_CHAT_ACTION_TTL_SECONDS=600`, `ADMIN_CHAT_STEP_TAIL=50`,
   `ADMIN_CHAT_STEP_READ_BYTES=65536`, `ADMIN_CHAT_AUDIT_RETENTION_DAYS=14` (D-6).
   `MLFLOW_HERMES_CHAT_EXPERIMENT` defaults to `"deepfix_hermes_chat"`.
2. Given `docker-compose.yml`, when the `backend-api` service is parsed, then its environment contains
   `HERMES_CHAT_ENABLED`, `HERMES_CHAT_MODEL`, `HERMES_CHAT_API_SERVER_KEY`, `HERMES_CHAT_BASE_URL`,
   `MLFLOW_HERMES_CHAT_EXPERIMENT`, and every `ADMIN_CHAT_*` name from AC 1. Every non-`str` field uses a
   non-empty Compose default (`${HERMES_CHAT_ENABLED:-false}`, `${ADMIN_CHAT_MAX_PROMPT_CHARS:-48000}`, …) and
   only `str` fields may use `${VAR:-}`.
3. Given `compose.hermes.yml`, when the `hermes` service is parsed, then its environment contains
   `HERMES_CHAT_ENABLED`, `HERMES_CHAT_MODEL`, `MLFLOW_HERMES_CHAT_EXPERIMENT`, and still `HERMES_MODEL`.
4. Given every chat variable unset, when the parsed Compose environment is turned into `Settings()`, then
   construction succeeds and no field raises `bool_parsing` or `int_parsing`.
5. Given `example.env`, when it is read, then it documents `HERMES_CHAT_ENABLED=false`,
   `HERMES_CHAT_MODEL` (commented out), `HERMES_CHAT_BASE_URL` (commented out, default shown),
   `MLFLOW_HERMES_CHAT_EXPERIMENT`, `ADMIN_CHAT_AUDIT_RETENTION_DAYS=14` (with a comment that `0` keeps audit
   rows forever, D-6), and every other `ADMIN_CHAT_*` name, each with a one-line comment.
6. Given the tree, when a plumbing test runs, then every `ADMIN_CHAT_*`, `HERMES_CHAT_*`, and
   `MLFLOW_HERMES_CHAT_EXPERIMENT` name read by `Settings` or by `sandbox_boot.py` appears in `example.env` and
   in the matching Compose service environment (`backend-api` for API reads, `hermes` for boot reads).

Profile config and boot:

7. Given `chat_profile_config(...)`, when the returned mapping is inspected, then it contains
   `gateway.standalone` set to `True`, `platform_toolsets.api_server` equal to
   `("skills", "web", "vision", "todo", "memory")`, and no `terminal`, `file`, or `code_execution` entry.
8. Given a written chat `config.yaml` that lacks `gateway.standalone`, when `apply_sandbox` runs, then it
   raises `SandboxBootError` and does not write `gateway_state.json`.
9. Given `HERMES_CHAT_ENABLED=true`, when `apply_sandbox` runs, then it starts the chat gateway and the profile
   listens on 8643; the exact command used (`hermes -p chat gateway run` foreground versus the s6 per-profile
   slot) and whether it survives `docker restart` are recorded in the verification note. Given the same boot
   with `HERMES_CHAT_ENABLED=false` or unset, then the start command is not invoked and
   `profiles/chat/gateway_state.json` is exactly `{"gateway_state":"stopped"}`.
10. Given a started boot, when the profile's `config.yaml` and `.env` are inspected, then the chat `.env`
    contains `API_SERVER_ENABLED=true`, `API_SERVER_HOST=0.0.0.0`, `API_SERVER_PORT=8643`, and `API_SERVER_KEY`
    equal to `HERMES_CHAT_API_SERVER_KEY`; it contains no `OPENROUTER_API_KEY`, no `S3_*`, no `LLM_API_KEY`, and
    its `API_SERVER_KEY` differs from the optimization profile's.
11. Given `HERMES_CHAT_API_SERVER_KEY` shorter than 16 characters or containing a placeholder,
    when `apply_sandbox` runs with chat enabled, then it raises through `validate_api_key`.
12. Given the three cases — `HERMES_CHAT_MODEL` set, blank, unset — when the chat profile's model is resolved during
    `apply_sandbox` and written into `profiles/chat/config.yaml`, then the value is the chat model for the first
    case and the `HERMES_MODEL` value for the other two. `chat_profile_config` keeps taking an explicit `model`
    argument; the fallback is computed in `apply_sandbox`, so the resolver is one small function with three
    test cases and no hidden default.
13. Given `tests/test_hermes_sandbox.py::test_chat_profile_env_is_replaced`, when it runs after this story,
    then it asserts (a) the chat `.env` `API_SERVER_KEY` equals `HERMES_CHAT_API_SERVER_KEY`, (b) it does not
    equal the optimization `API_SERVER_KEY`, (c) the optimization `.env` still carries its own key, and (d) no
    `OPENROUTER_API_KEY`, `S3_`, or `LLM_API_KEY` appears in the chat file. Its `_env()` fixture gains
    `HERMES_CHAT_API_SERVER_KEY` (`"b" * 32`).
14. Given the four prose sites that still describe the profile as idle — `example.env:103`,
    `compose.hermes.yml:4`, `docker/hermes/Dockerfile:37`, and the chat `SOUL.md` text written by
    `sandbox_boot.py` — when they are read after this story, then none contains "not started" or
    "future admin chat", and each says the profile is a standalone gateway gated on `HERMES_CHAT_ENABLED`. A test
    greps those four sources for both phrases and fails on a match.
15. Given `src/deepfix_rag/api/routes/admin_chat.py` exists with the router-level gate from this story, when its
    module constants and predicate are inspected, then it exposes `DISABLED_CODE = "chat_disabled"`,
    `DISABLED_MESSAGE = "Hermes chat is turned off on this server."`,
    `GATE_EXEMPT_PATHS = ("/admin/chat/audit", "/admin/chat/threads/{thread_id}")` for the two SQLite-only
    routes, and `require_chat_enabled(settings)` which raises 503 with
    `detail={"code": "chat_disabled", "message": DISABLED_MESSAGE}`. A unit test asserts the predicate's status,
    code, and exact sentence, and that both exempt paths are in the constant. Applying the gate to the nine
    Hermes-facing routes is CHAT-003's work, because only one of them exists at this commit; CHAT-012 checks all
    nine on the deployment. Identity and role are checked before the gate — a dependency ordering CHAT-003
    implements and tests.
16. Given the optimization profile, when its config is compared before and after this story, then
    `agent.max_turns` is still 40 and `OPTIMIZATION_AGENT` is still the single literal `"hermes"`.
17. Given a running stack with `HERMES_CHAT_ENABLED=true`, when `GET http://hermes:8643/health` is called from
    `backend-api`, then it returns `{"status": "ok"}`; when
    `POST http://hermes:8643/v1/chat/completions` is called with the chat bearer and
    `{"model": "hermes-agent", "messages": [{"role": "user", "content": "say ok"}]}`, then it returns a
    `chat.completion`; and `POST http://hermes:8642/p/chat/v1/chat/completions` does not work. Recorded in the
    verification note.
18. Given chat disabled, when the container process list and listening sockets are inspected, then exactly one
    gateway process runs (the optimizer's, matched on arguments and profile — not on a literal `hermes gateway`
    name) and nothing is bound to 8643. Recorded in the verification note with container RSS in both states for
    information only.

## Test commands

```bash
# Settings, plumbing, config, boot: unit and integration
uv run --env-file .env pytest -v tests/test_hermes_sandbox.py
uv run --env-file .env pytest -v tests/test_hermes_chat_profile.py
uv run --env-file .env pytest -v tests/test_hermes_dockerfile.py
uv run --env-file .env pytest -v tests/test_hermes_optimization.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check
```

AC 17 and AC 18 need a container runtime. This VM has none (`docker`, `podman`, `nerdctl` are absent), so they
are recorded as not-run here and repeated on the deployment in CHAT-012, exactly as
`tasks/prd-hermes-optimizer.md` US-007 recorded its container checks.

## Out of scope
- Any HTTP route on deepfix-api beyond the disabled-gate predicate (CHAT-003 builds the routes).
- The chat store (CHAT-002), the prompt (CHAT-004), the UI (CHAT-010).
- Chat tracing plugins and the `deepfix_hermes_chat` experiment creation (CHAT-011). This story only adds the
  `MLFLOW_HERMES_CHAT_EXPERIMENT` setting and its plumbing.
- Changing `mem_limit: 4g`, `cpus: "2"`, `agent.max_turns: 40`, or `OPTIMIZATION_RUN_TIMEOUT_HOURS`.
- Editing `tasks/prd-hermes-optimizer.md` (its amendments land with CHAT-011).

## Notes
- The settings module is `src/deepfix_rag/config/settings.py`; the module config is
  `SettingsConfigDict(extra="ignore")`, so a name missing from the Compose allowlist is silently invisible —
  AC 2 and AC 6 are what stop that.
- `SandboxBootError` is already defined in `docker/hermes/sandbox_boot.py`; the standalone check belongs next to
  the `_dump_yaml` of the chat config, after the write, so a rejected boot leaves no half-written profile that
  later starts unguarded.
- The chat profile's `.env` is written with `replace=True`. Keep that: it is what stops an optimization key from
  an older boot lingering.
- `chat_profile_config` currently does not read `HERMES_CHAT_MODEL`; the fallback is
  `env.get("HERMES_CHAT_MODEL", "").strip() or model_id(env)` computed in `apply_sandbox` and passed in as
  `model=`.
- `example.env:104` already ships `HERMES_CHAT_API_SERVER_KEY=`; do not add a second line.
- The disabled-gate predicate is a small function in the chat router module (`chat_enabled(settings)` plus a
  dependency that raises the 503 with `code: "chat_disabled"`), unit-tested here and wired to the routes in
  CHAT-003. Keep the two exempt route paths as constants so CHAT-003 and CHAT-012 cannot drift.

## Definition of done
- [ ] Code complete
- [ ] Tests updated or added (including the rewritten `test_chat_profile_env_is_replaced`)
- [ ] Docs updated: `example.env`, the four prose sites, and a verification note appended to this file
- [ ] `uv run ruff check`, `uv run ruff format --check`, and the fast pytest suite pass
- [ ] Committed on `feat/hermes-optimizer` on its own, before CHAT-002 starts

## Verification note
Fill in on implementation, in the style of `tasks/prd-hermes-optimizer.md`'s story notes: the start command the
image actually uses, whether it survives `docker restart`, the two `/p/chat/` negative checks, the process and
port evidence in both states, and which ACs could not be run on the implementing VM.

Also record the measurement the PRD's open question 1 assigns to whoever runs this story: close the socket
mid-turn and observe whether the gateway keeps generating (container token usage or the gateway log). The behaviour
shipped is the recommended default either way — close the upstream stream, record `cancelled`, keep the partial
text — so this is a measurement, not a decision gate. If the answer is "it keeps running", record it here; the
300-second cap in CHAT-003 is then the only bound, and the risk is accepted rather than fixed.

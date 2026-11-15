# ASKCHAT-005 — Bound one member and one deployment, and plumb every bound

## Type
Feature

## Status
Draft

## Summary
One member cannot hold the API, one deployment cannot be overrun, and every bound is visible in the three places an operator looks: `Settings`, the Compose environment, and `example.env`. There is no Ask-chat model setting: the chat resolves `LLM_MODEL`, the same value eval and `POST /ask` use.

## PRD traceability
- Related: FR-026, FR-027, FR-028
- Decisions: D-8 (the limits and their defaults), D-7 (resolve `LLM_MODEL`; no `ASK_CHAT_MODEL`)
- NFR touchpoints: NFR-3 (no new container, no cap change), NFR-8 (one line per refusal), NFR-4 (a refusal writes no row)

## Dependencies
- Requires: ASKCHAT-003 (turns to count), ASKCHAT-004 (timeouts settle a turn).
- Blocks: ASKCHAT-006 (the client renders these refusals).

## Acceptance criteria

**The settings**

1. Given `src/deepfix_rag/config/settings.py`, when it is read, then `ASK_CHAT_MAX_TURNS_PER_WINDOW` (20), `ASK_CHAT_TURN_WINDOW_SECONDS` (300), `ASK_CHAT_MAX_CONCURRENT_TURNS` (4), `ASK_CHAT_MAX_TOOL_CALLS` (3), `ASK_CHAT_MAX_HISTORY_MESSAGES` (12), `ASK_CHAT_MAX_MESSAGE_CHARS` (4000), `ASK_CHAT_MAX_PASSAGE_CHARS` (1200), and `ASK_CHAT_TURN_TIMEOUT_SECONDS` (120) all exist with those defaults, and each has a one-line comment saying what it bounds.
2. Given `docker-compose.yml`, when the `backend-api` service's environment is read, then every one of those names appears with a non-empty default (`${ASK_CHAT_MAX_TOOL_CALLS:-3}` and so on), because that service has an explicit allowlist and no `env_file`, so a name that is only on `Settings` is invisible on the deployment.
3. Given `example.env`, when it is read, then every one of those names is documented with a one-line comment. The only other change is one comment line beneath `LLM_MODEL` naming `qwen/qwen3.8-27b` as the manual tool-calling fallback — a comment, not a variable.

**No model setting (D-7)**

4. Given `Settings`, when its fields are inspected, then no field matches `ASK_CHAT_.*MODEL`, and given `example.env`, no *variable* line matches it either. This is asserted by a test, so a later story cannot reintroduce the setting quietly.
5. Given `LLM_MODEL` set to a fixture id and `Settings()` built, when the loop resolves its model, then it is that fixture — no default, no fallback, and no second variable. The chat and `POST /ask` resolve the same value on purpose, so what the gate scores and what members are served cannot diverge.

**The limits**

6. Given a member who has made `ASK_CHAT_MAX_TURNS_PER_WINDOW` turns inside `ASK_CHAT_TURN_WINDOW_SECONDS`, when they ask again, then the response is 429 with a `Retry-After` and a sentence naming the wait.
7. Given that refusal, when the database is read, then no turn row was written for it, so a refusal cannot ratchet itself into a longer ban.
8. Given `ASK_CHAT_MAX_CONCURRENT_TURNS` turns already running across the deployment, when another member asks, then 429 with a `Retry-After`, and the sentence says the server is busy rather than blaming the member's own usage.
9. Given a turn that runs past `ASK_CHAT_TURN_TIMEOUT_SECONDS`, when it is ended, then the stream emits an `error` part and a terminal `data-deepfix-turn` with `status: "failed"`, and the row is `failed` with an `ended_at` — never `running`.
10. Given a message longer than `ASK_CHAT_MAX_MESSAGE_CHARS`, when it is sent, then 400 with a sentence naming the limit.
11. Given a stored history longer than `ASK_CHAT_MAX_HISTORY_MESSAGES`, when a turn is built, then the oldest messages are trimmed rather than the request refused, and the newest `ASK_CHAT_MAX_HISTORY_MESSAGES` messages are used.

**No new footprint**

12. Given all Compose files, when they are read after this story, then no new service and no new container exists, and `hermes` (4 GB, `cpus: "2"`), `mlflow` (2 GB), `rustfs` (1 GB), and `diagnostic-agent` (1 GB) are unchanged. `backend-api` still has no `mem_limit`.

**The required test**

13. Given the real app, when `tests/test_ask_chat_limits.py` runs, then: 20 turns in a window pass and the 21st is 429 with `Retry-After`; a fifth concurrent turn is 429 while four are open; a slow turn hits the timeout and settles `failed`; a 4001-character message is 400; a `docker-compose.yml` composed with every `ASK_CHAT_*` unset still builds `Settings()`; and the plumbing test fails when a setting read by `Settings` is missing from the Compose environment or `example.env`. No mocks.
14. Given the same run, when the model guard is checked, then `Settings` has no field and `example.env` no variable line matching `ASK_CHAT_.*MODEL`, and a turn streamed against the fixture endpoint reports the `LLM_MODEL` fixture id as its model.

## Out of scope
- Per-member quotas, billing, or tiered limits. The window is the whole budget.
- Fair queuing between members. A refusal is the answer, not a wait.
- Changing any existing limit for the admin chat, the optimizer, or the index.
- A rate-limit table. The count reads `ask_chat_turns`, which ASKCHAT-003 already writes, so no new storage is added.

## Notes
- The limiter mirrors `admin_chat.check_rate_limit`: count the member's rows in the window, then the global running count, and refuse without writing. `now` must be injectable so a test never sleeps out a window.
- The plumbing test's shape is CHAT-001's lesson: `docker-compose.yml` gives `backend-api` an explicit environment allowlist with no `env_file`, so a name added only to `Settings` silently takes its default on the deployment. The test fails when a setting read by `Settings` is absent from either file, which is the only way that mistake is caught before someone wonders why their change did nothing.
- Typed settings reject an empty string: a `bool` with `FLAG=''` raises `bool_parsing` and an `int` with `N=''` raises `int_parsing`. Every integer default in the Compose line is therefore non-empty, matching how `OPTIMIZATION_MIN_DEV_QUESTIONS: ${…:-50}` already does it. Only string fields may use `${VAR:-}`.
- D-7 removed the model setting after the owner's decision. The guard in AC 4 exists so the reversal cannot happen by accident in a later story; the comment in `example.env` is deliberately a comment so the guard still passes.

## Definition of done
- [ ] Code complete
- [ ] `tests/test_ask_chat_limits.py` green against the real app, including the plumbing test and the model guard
- [ ] `example.env` and `docker-compose.yml` both updated; `docs/deployment.md` unchanged because no cap moved
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run --env-file .env pytest -m "not network and not e2e"` pass
- [ ] Committed on its own before ASKCHAT-006 starts

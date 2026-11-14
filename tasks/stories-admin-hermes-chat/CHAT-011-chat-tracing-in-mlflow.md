# CHAT-011 — Chat tracing in MLflow

## Type
Feature

## Status
Draft

## Summary
Each chat turn becomes an MLflow trace in its own `deepfix_hermes_chat` experiment, carrying the thread and turn id
and no conversation text, so a bad answer can be inspected without reading container logs — while the optimizer's
`deepfix_hermes` experiment and its tags keep working unchanged.

## PRD traceability
- Related: FR-024
- Decisions: D-5 (a separate `deepfix_hermes_chat` experiment)
- Journeys: none directly; this is the G6 traceability requirement
- NFR touchpoints: NFR-5 (content capture off; the SQLite store is the transcript of record), NFR-4 (no key in a
  span attribute)
- Amends: `tasks/prd-hermes-optimizer.md` US-007's follow-up note, which says the chat profile gets no plugins and
  no `hermes_otel.yaml`. That is reversed here, in the same commit as the code, with the reason D-5 gives.

## Dependencies
- Requires: CHAT-001 (the chat profile's boot path and its Compose environment), CHAT-004 (the exact prompt
  sentence carrying the ids).
- Blocks: nothing. CHAT-012 checks that the trace exists on the deployment, so it is implemented after this story.

## Acceptance criteria

Plugins and config:

1. Given a boot with chat enabled, when the chat profile's home is inspected, then it has the same two plugins as
   the optimization profile — `hermes-otel` and `deepfix-run-tag` — symlinked into `profiles/chat/plugins/` and
   enabled in `profiles/chat/config.yaml`.
2. Given `profiles/chat/hermes_otel.yaml`, when it is read, then it has `content_capture: "off"`,
   `capture_conversation_history: false`, `capture_sender_id: false`, and one `type: mlflow` backend aimed at
   `MLFLOW_TRACKING_URI` with the chat experiment's id (or `experiment_id_env` pointing at the chat profile's own
   env name). It is a separate file from the optimization profile's; neither profile reads the other's.
3. Given `MLFLOW_HERMES_CHAT_EXPERIMENT` (default `deepfix_hermes_chat`), when boot runs and MLflow answers, then
   the experiment is created and a test asserts the chat experiment name differs from `HERMES_TRACE_EXPERIMENT`
   (`deepfix_hermes`). Boot reads the variable inside the `hermes` service (plumbed in CHAT-001).
4. Given `ensure_hermes_trace_experiment(tracking_uri)` in `src/deepfix_rag/hermes_trace_experiment.py` hardcodes
   `HERMES_TRACE_EXPERIMENT` today, when this story lands, then it gains a `name` parameter; the chat call passes
   the chat experiment name; the existing optimizer call keeps its default and `tests/test_hermes_run_tag.py`
   stays green.
5. Given a boot with chat disabled, when the profile home is inspected, then no plugin is loaded and no experiment
   is created — the off path stays as cheap as CHAT-001 made it.
6. Given a failure to create the experiment (MLflow down), when boot runs, then it logs one line and does not stop
   the container, matching the optimizer path.

Tracing:

7. Given the server prompt from CHAT-004, when it contains
   `The DeepFix chat thread id is <thread_id> and the turn id is <turn_id>.`, then the `deepfix-run-tag` plugin
   parses that sentence and sets `deepfix.chat_thread_id` and `deepfix.chat_turn_id` on the spans of that turn.
8. Given an optimization round, when the plugin runs for it, then `deepfix.run_id` still works exactly as before,
   and `tests/test_hermes_run_tag.py`'s existing cases still pass.
9. Given the plugin's secret scrubbing, when a span attribute or event text contains `API_SERVER_KEY`,
   `HERMES_CHAT_API_SERVER_KEY`, or `OPENROUTER_API_KEY`, then it is scrubbed; the scrub logic is unchanged apart
   from accepting the new chat attributes.
10. Given a trace, when it is exported, then it carries the two chat attributes and no key; a scripted turn run
    through the plugin hooks (as `tasks/prd-hermes-optimizer.md` US-007 did) asserts both.
11. Given content capture stays off, when a trace is inspected, then it carries no prompt text and no reply text.
    The SQLite store remains the transcript of record.

Tests:

12. Given the suite, when it runs, then it covers: the chat yaml's four capture settings; the two experiment names
    differ; `ensure_hermes_trace_experiment(name=...)` creating a named experiment against a temp SQLite MLflow
    tracking URI; a scripted turn producing both chat attributes; the optimizer's `deepfix.run_id` still stamped;
    no key in any attribute.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_hermes_run_tag.py
uv run --env-file .env pytest -v tests/test_hermes_chat_trace.py
uv run --env-file .env pytest -v tests/test_hermes_sandbox.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check
```

The tests use a temp-sqlite MLflow tracking URI and import the plugin module by file path, exactly as
`tests/test_hermes_run_tag.py` already does; no Hermes process and no container are involved.

## Out of scope
- Tracing the proxy route or the deepfix-api side. The trace is the Hermes agent turn, as US-007 defined it for
  optimization.
- Capturing prompts or replies in MLflow (explicitly forbidden by NFR-5 and AC 11).
- MLflow authentication (the optimizer PRD keeps MLflow unauthenticated for this release).
- Changing `deepfix eval`'s `MLFLOW_DEV_RAG_EXPERIMENT`.

## Notes
- The prompt sentence is a contract between two components. Put it in one place — a constant used by both
  `admin_chat.system_prompt` and the plugin's parser — or, if the plugin cannot import the package (it is loaded
  by the Hermes virtualenv), add a test that asserts the literal appears in both files. Silent drift here means
  traces with no thread id.
- The plugin currently derives `deepfix.run_id` from tool text and attributes. Keep that code path and add the chat
  branch beside it, chosen by which sentence is present, rather than replacing it.
- `_install_trace_plugins(home, config, env)` takes the home; call it for the chat profile with the chat home and
  the chat experiment id. Keep `_chown_hermes` after every write so the gateway user can read the files.
- This story is the one that edits the amendment list's items for `tasks/prd-hermes-optimizer.md`. Do not reopen or
  uncheck its US-001…US-010; only the US-007 follow-up sentence about the chat profile's plugins and the Design
  Considerations pointer change, as the chat PRD's "Amendments required" section specifies.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; `tests/test_hermes_run_tag.py` unchanged and green
- [ ] Docs updated: the two `tasks/prd-hermes-optimizer.md` amendments this story owes (Design Considerations
      pointer, US-007 follow-up), and `example.env`'s `MLFLOW_HERMES_CHAT_EXPERIMENT` comment names the separate
      experiment
- [ ] `uv run ruff check`, `uv run ruff format --check`, and the fast pytest suite pass
- [ ] Committed on its own before CHAT-012 starts

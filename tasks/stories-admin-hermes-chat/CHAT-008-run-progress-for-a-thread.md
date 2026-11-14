# CHAT-008 — Run progress for a thread

## Type
Feature

## Status
Draft

## Summary
An admin can follow a multi-hour optimization job from the chat thread: its status, its reason, its deltas, the
MLflow link, and the tail of its step log — and the panel still shows the right thing after a reload, while the
job runs, and when the job has died.

## PRD traceability
- Related: FR-016
- Decisions: none; implements the "job plane" section and J2 steps 3–5
- Journeys: J2 step 4 (reopen the thread and keep polling), J2 step 5 (final status, deltas, MLflow link)
- NFR touchpoints: NFR-3 (bounded read, not a whole-file load), NFR-5 (no holdout text, no private path), NFR-1
  (`fail_stale_runs` so a dead job is not shown as running)

## Dependencies
- Requires: CHAT-002 (`admin_chat_thread_runs`, `thread_runs`), CHAT-007 (a confirmed action is what produces a
  run to link).
- Blocks: CHAT-010 (the run panel), CHAT-012 (J2 end to end).

## Acceptance criteria

Link and list:

1. Given `POST /admin/chat/threads/{thread_id}/runs/{run_id}` (`operation_id="link_admin_chat_thread_run"`), when
   the thread is the caller's and the run exists in `optimization_runs`, then the pair is linked. Given an unknown
   `run_id`, then 404. Given the same pair twice, then 200 both times and one row.
2. Given `GET /admin/chat/threads/{thread_id}/runs` (`operation_id="list_admin_chat_thread_runs"`), when an admin
   calls it, then each item is `run_id`, `status`, `trigger`, `reason`, `started_at`, `ended_at`,
   `primary_metric`, `dev_delta`, `holdout_delta`, `holdout_wins`, `holdout_losses`, `mlflow_run_id`,
   `mlflow_url`, and `steps`. Given another admin's thread, then 404.

Step tail:

3. Given a linked run with a step log at `PRODUCT_DATA_DIR/optimization-logs/<run_id>.log`, when the tail is read,
   then it returns the last `ADMIN_CHAT_STEP_TAIL` (50) lines, each redacted and truncated to 500 characters.
4. Given the read, when it is performed, then it seeks to the end and reads backwards at most
   `ADMIN_CHAT_STEP_READ_BYTES` (65536) bytes and splits lines; a 2 MB log test asserts the number of bytes read
   stays under the bound and that no read of the whole file occurs.
5. Given a step log containing a secret, a holdout question, and the `----- output -----` section, when the tail is
   returned, then none of the three appears in the response.
6. Given a run with no log file yet, when the tail is read, then `steps` is an empty list and the response is 200.

Stale runs:

7. Given `GET`, when it is handled, then it calls `fail_stale_runs` with the same arguments
   `GET /admin/optimization` uses (timeout from `OPTIMIZATION_RUN_TIMEOUT_HOURS`, `options=settings_from(settings)`),
   so a dead job is not reported as `running` forever.

Delete interaction:

8. Given a thread delete, when it runs, then only the `admin_chat_thread_runs` rows go; `optimization_runs` rows and
   the step log file are untouched.

Route inventory (NFR-4):

9. Given the app after this story, when a test walks `app.routes`, then exactly eleven paths start with
   `/admin/chat` — `POST /admin/chat/completions`, `GET /admin/chat/turns/{turn_id}/stream`,
   `GET /admin/chat/threads`, `POST /admin/chat/threads`, `GET /admin/chat/threads/{thread_id}`,
   `DELETE /admin/chat/threads/{thread_id}`, `GET /admin/chat/threads/{thread_id}/runs`,
   `POST /admin/chat/threads/{thread_id}/runs/{run_id}`, `POST /admin/chat/actions/{action_id}/confirm`,
   `POST /admin/chat/actions/{action_id}/reject`, and `GET /admin/chat/audit` — every one is registered with the
   admin dependency, none is unauthenticated, and the set that skips the enabled and configuration gates is
   exactly `GET /admin/chat/audit` and `DELETE /admin/chat/threads/{thread_id}`. A twelfth path, or an exempt
   third, fails the test.

Tests:

10. Given the suite, when it runs, then it covers: two linked runs come back newest first; a run linked to another
    thread is not returned for this one; the step tail is capped and redacted; a member gets 403; the 2 MB log
    stays under the byte bound; an unknown `run_id` is 404; linking twice is idempotent.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_runs.py
uv run --env-file .env pytest -v tests/test_api_optimization.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck
```

Use `tmp_path` for `PRODUCT_DATA_DIR` and write the step log with the same line shape `_step_line` produces, so the
redaction path is the real one.

## Out of scope
- Streaming the step log. The panel polls; the PRD rejects streaming from Hermes.
- Paging the full step log (open question 2's recommended default is no; a follow-up story would add
  `GET /admin/chat/runs/{run_id}/steps?limit&before`).
- Any write to `optimization_runs` other than what `fail_stale_runs` already does.
- Reading an `output` section, holdout content, or the `run_bundle` S3 object.

## Notes
- The bounded reader belongs in `admin_chat` as a small function with the byte bound as an argument, so AC 4's
  test can pass a tiny bound and assert the slice. Do not reuse a line-oriented `read_text()` helper.
- `fail_stale_runs` currently lives in `optimization.py`; call it, do not duplicate its rules. The parity test can
  assert the same keyword arguments are passed as the optimization route passes.
- `mlflow_url` is built the way `GET /admin/optimization` builds `OptimizationRunSummary.mlflow_url`; reuse that
  helper rather than a second URL builder.
- The tail must never include the `----- output -----` marker section (US-007 of the optimizer PRD appends the
  final output under that marker, up to 64 KiB). Cut at the marker, then take the last 50 lines.
- Newest first: order by `started_at` descending, with `run_id` as a tiebreak so a test is deterministic.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; the byte bound asserted, not assumed
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated if the step-tail caps are documented in `example.env`
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, and `bun run typecheck` pass
- [ ] Committed on its own before CHAT-009 starts

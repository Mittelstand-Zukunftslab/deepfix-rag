# CHAT-006 — Rate limit and audit read

## Type
Feature

## Status
Draft

## Summary
A stuck browser tab, a prompt loop, or two careless admins cannot exhaust the model budget or the 4 GB container,
every chat-initiated action leaves a durable, redacted audit row the operator can read back — including who
started a run whose conversation has since been deleted — and that trail ages out on its own after the retention
window, whether chat is on or off.

## PRD traceability
- Related: FR-017, FR-018, FR-030 (the scheduler wiring; CHAT-002 ships the prune function)
- Decisions: D-4 (audit rows survive a delete and report `thread_deleted`; audit route stays available when chat
  is off), D-6 (the prune is by age, from the scheduler tick, and runs with chat off)
- Journeys: J4 step 4 (the audit view after a delete)
- NFR touchpoints: NFR-2 (chat's share of the 4 GB envelope), NFR-4 (audit is admin-only), NFR-5 (no message
  text in audit; the trail is bounded in time), NFR-8 (a refusal is one structured log line, and so is a prune
  that deleted something)

## Dependencies
- Requires: CHAT-002 (`count_recent_turns`, `record_audit`, `prune_audit`, the audit `CHECK` list), CHAT-003 (the
  route the limiter wraps), CHAT-005 (threads and turns to count against).
- Blocks: CHAT-007 (action audit kinds are written through the same helper and the limiter must not be bypassed
  by the confirm route), CHAT-009 (the client reads the 429 shape), CHAT-010 (the audit view, if built).

## Acceptance criteria

Per-admin limits:

1. Given `ADMIN_CHAT_MAX_TURNS_PER_WINDOW` (30) inside `ADMIN_CHAT_TURN_WINDOW_SECONDS` (300), when an admin's
   persisted turns in the window reach the cap and a new turn is requested, then 429 with a readable sentence
   naming the wait, a `Retry-After` header in seconds, and one `rate_limited` audit row.
2. Given an admin with a `running` turn, when they start a second turn, then 429 with a readable sentence. The
   count comes from `admin_chat_turns`, so it holds across processes and restarts.
3. Given repeated refusals inside one minute, when the admin retries five times, then at most one `rate_limited`
   audit row is written per minute per admin. The "already wrote one" check is a `SELECT` on the newest
   `rate_limited` row for that admin, and it never triggers a second limiter pass.

Global cap:

4. Given `ADMIN_CHAT_MAX_CONCURRENT_TURNS` (2), when a third turn is requested across admins, then 429 with
   "The assistant is busy. Try again in a moment." and no audit row.
5. Given the limiter's count, when a `rate_limited` row is written, then the count does not change, because it
   reads `admin_chat_turns` only. A refusal cannot ratchet itself.

Audit read:

6. Given `GET /admin/chat/audit?limit=50` (`operation_id="list_admin_chat_audit"`), when an admin calls it, then
   it returns the newest audit rows with `admin_email`, `thread_id`, `turn_id`, `kind`, `detail`, and
   `thread_deleted`. Given a member, then 403. Given chat disabled, then it still returns 200 (D-4).
7. Given a row whose `thread_id` no longer exists in `admin_chat_threads`, when the route reads it, then
   `thread_deleted` is `true`, computed at read time with one `LEFT JOIN`, not stored.
8. Given a deleted thread's rows, when they are returned, then `kind` and `detail` are intact, `turn_id` is
   returned as stored even though it is not resolvable, and no message text appears anywhere in the response.

Audit content:

9. Given every audit write, when `detail` is stored, then it passed through the redaction helper and is at most
   500 characters (`STEP_PREVIEW_LIMIT`).

Retention wiring (D-6, FR-030):

10. Given `OptimizationScheduler`, when a `prune_audit` callable is passed at construction, then it is invoked from
    the thread's loop at most once per hour, **before** the due-run check so a slow prune cannot delay a run, and
    the call is skipped entirely when the callable is `None` (the default), so no existing scheduler test changes.
11. Given the loop, when the next scheduled run is 24 hours away (the default 02:00 cron), then the loop still
    wakes within an hour: `_idle_delay(seconds_until_next_run: float | None) -> float` returns `3600` for `None`
    (a paused schedule) and `min(seconds_until_next_run, 3600)` otherwise. A unit test asserts 3600 for a 24-hour
    gap, the real gap for a 90-second gap, and 3600 rather than 0 or a negative for `None`. Without this cap a
    prune tied to the tick would run about once a day at whatever hour the run happens, which is not a retention
    policy.
12. Given `HERMES_CHAT_ENABLED=false`, when a tick runs, then the prune still runs and still deletes rows past the
    window. The scheduler has no knowledge of the chat flag. A test constructs the scheduler with the flag off,
    inserts an audit row past the window, drives one tick, and asserts the row is gone.
13. Given two ticks inside the same hour, then the injected callable is invoked once. The guard is an in-memory
    last-run timestamp on the scheduler instance, so a restart prunes on its first tick.
14. Given the injected callable raises, when a tick runs, then the exception is caught and logged
    (`LOGGER.exception`) and the tick returns normally; the scheduler thread survives. This matches the existing
    `Scheduled optimization failed` handling.
15. Given a prune that deleted `n > 0` rows, then one structured line `chat audit prune deleted=<n>
    retention_days=<d>` is logged; when `n == 0`, nothing is logged (NFR-8).
16. Given `ADMIN_CHAT_AUDIT_RETENTION_DAYS` is `0`, when a tick runs, then nothing is deleted and no line is
    written. The setting is read on each invocation, not captured at construction, so a change takes effect on
    the next tick.

Tests:

17. Given the suite, when it runs, then it covers: the 31st turn in a window is 429 with `Retry-After`; two
    `running` turns for one admin is 429; the global cap with two admins; a member gets 403 on the audit route;
    a `detail` containing a secret is stored redacted; a deleted thread's rows come back with
    `thread_deleted: true` and no message text; the audit route answers with chat disabled and with the chat key
    unset; and the six retention-wiring cases (AC 10–16).

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_limits.py
uv run --env-file .env pytest -v tests/test_admin_chat_routes.py -k "audit or rate"
uv run --env-file .env pytest -v tests/test_optimization_schedule.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck
```

Use a temp SQLite file and a controllable clock (pass `now` into the limiter rather than sleeping); no test may
sleep 300 seconds. For the scheduler cases, drive `_loop`'s tick body and `_idle_delay` directly rather than
starting the thread and waiting — no test may sleep an hour either.

## Out of scope
- The prune implementation itself (CHAT-002's `prune_audit`).
- Pruning transcripts. Retention is audit-only; conversations are bounded by thread delete (D-4).
- Per-admin quotas beyond the window and concurrency caps.
- A UI for the audit view. The PRD lists the audit read as an API route; CHAT-010 builds the chat page only. If
  the audit is shown in the UI, that is a follow-up story, not this one.
- Any change to the job-plane limits (`start_optimization_run`'s `RunInProgress`).
- Making the loop's hourly wake configurable. `3600` is a constant; if it ever needs tuning, it becomes a setting
  in a follow-up rather than a hidden magic number now.

## Notes
- Put the limiter in `admin_chat` as a unit-testable function,
  `check_rate_limit(db_path, admin_id, *, now, max_turns, window_seconds, max_concurrent)` returning either a
  refusal (with reason and wait) or `None`; the route maps the refusal to 429. The route must not compute counts
  itself.
- `Retry-After` is the seconds until the oldest turn in the window falls out of it, rounded up, minimum 1.
- The global concurrency count is `SELECT COUNT(*) FROM admin_chat_turns WHERE status='running'`; it must not
  include `cancelled` or `failed` rows.
- `thread_deleted` uses `LEFT JOIN admin_chat_threads t ON t.thread_id = a.thread_id` and
  `t.thread_id IS NULL`. One query, no per-row lookup.
- Audit `detail` for a limit refusal names the cap and the wait, never the admin's text.
- Add the prune hook to `OptimizationScheduler.__init__` as an optional keyword
  (`prune_audit: Callable[[], int] | None = None`), not as a subclass or a monkeypatched method: every existing
  scheduler test constructs the scheduler without it, so the default must be a no-op.
- `_idle_delay` is a small module-level helper rather than inline arithmetic in `_loop`, so its three cases are
  unit-testable without a thread. Keep `_loop`'s `if self._wake.wait(delay)` shape; only the value it waits on
  changes.
- Inject the callable from `app.py` as
  `lambda: admin_chat.prune_audit(settings.product_db_path, settings.ADMIN_CHAT_AUDIT_RETENTION_DAYS)`. Capture no
  `HERMES_CHAT_ENABLED` here; that is what makes AC 12 true by construction rather than by care.
- Log with the existing `LOGGER` (the scheduler already imports it from `deepfix_rag.config.settings`). One line,
  `deleted` and `retention_days` fields, no per-row output.
- Keep `tests/test_optimization_schedule.py`'s existing cases untouched; the new ones go beside them, and if any
  existing case breaks on the `_idle_delay` change, the case was asserting the old 24-hour wait and needs its
  expectation updated with a comment saying why.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; no `unittest.mock`, no real sleeping
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated if the limits are documented in `example.env` comments from CHAT-001 (add the default values),
      including the `ADMIN_CHAT_AUDIT_RETENTION_DAYS` comment naming the prune and its hourly tick
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, and `bun run typecheck` pass
- [ ] Committed on its own before CHAT-007 starts

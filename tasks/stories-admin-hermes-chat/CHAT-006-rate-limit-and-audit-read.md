# CHAT-006 — Rate limit and audit read

## Type
Feature

## Status
Draft

## Summary
A stuck browser tab, a prompt loop, or two careless admins cannot exhaust the model budget or the 4 GB container,
and every chat-initiated action leaves a durable, redacted audit row the operator can read back — including who
started a run whose conversation has since been deleted.

## PRD traceability
- Related: FR-017, FR-018
- Decisions: D-4 (audit rows survive a delete and report `thread_deleted`; audit route stays available when chat
  is off)
- Journeys: J4 step 4 (the audit view after a delete)
- NFR touchpoints: NFR-2 (chat's share of the 4 GB envelope), NFR-4 (audit is admin-only), NFR-5 (no message
  text in audit), NFR-8 (a refusal is one structured log line)

## Dependencies
- Requires: CHAT-002 (`count_recent_turns`, `record_audit`, the audit `CHECK` list), CHAT-003 (the route the
  limiter wraps), CHAT-005 (threads and turns to count against).
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

Tests:

10. Given the suite, when it runs, then it covers: the 31st turn in a window is 429 with `Retry-After`; two
    `running` turns for one admin is 429; the global cap with two admins; a member gets 403 on the audit route;
    a `detail` containing a secret is stored redacted; a deleted thread's rows come back with
    `thread_deleted: true` and no message text; the audit route answers with chat disabled and with the chat key
    unset.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_limits.py
uv run --env-file .env pytest -v tests/test_admin_chat_routes.py -k "audit or rate"
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck
```

Use a temp SQLite file and a controllable clock (pass `now` into the limiter rather than sleeping); no test may
sleep 300 seconds.

## Out of scope
- Retention or purging of audit rows (open question; A6 in the PRD says the pilot keeps them unbounded).
- Per-admin quotas beyond the window and concurrency caps.
- A UI for the audit view. The PRD lists the audit read as an API route; CHAT-010 builds the chat page only. If
  the audit is shown in the UI, that is a follow-up story, not this one.
- Any change to the job-plane limits (`start_optimization_run`'s `RunInProgress`).

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

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; no `unittest.mock`, no real sleeping
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated if the limits are documented in `example.env` comments from CHAT-001 (add the default values)
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, and `bun run typecheck` pass
- [ ] Committed on its own before CHAT-007 starts

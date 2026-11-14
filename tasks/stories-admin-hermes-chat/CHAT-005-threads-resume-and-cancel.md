# CHAT-005 — Threads, resume, and cancel

## Type
Feature

## Status
Draft

## Summary
An admin's threads persist, a reply in progress survives a reload or a dropped connection, and Stop ends only the
reply. Closing the tab, aborting a stream, or restarting the API never loses the partial answer and never touches
an optimization run.

## PRD traceability
- Related: FR-008, FR-009, FR-010, FR-011, FR-012
- Decisions: D-4 (delete is a hard delete, exposed on the chat router)
- Journeys: J1 step 1 (thread list open), J3 (stop and retry), J4 step 1–3 (delete)
- NFR touchpoints: NFR-1 (no turn left `running` after a restart), NFR-3 (thread load is two indexed reads)

## Dependencies
- Requires: CHAT-002 (`create_thread`, `list_threads`, `get_thread`, `delete_thread`, `fail_interrupted_chat_turns`),
  CHAT-003 (the completions route and its turn lifecycle).
- Blocks: CHAT-006 (the audit read is tested against threads this story creates), CHAT-009 (the client calls these
  routes), CHAT-010 (history list and reconnect).

## Acceptance criteria

Thread routes:

1. Given `GET /admin/chat/threads` (`operation_id="list_admin_chat_threads"`), when an admin calls it, then it
   returns that admin's threads newest first, `limit` defaulting to 50, each with `thread_id`, `title`,
   `updated_at`, and `last_turn_status`.
2. Given `GET /admin/chat/threads/{thread_id}` (`get_admin_chat_thread`), when the thread belongs to the caller,
   then it returns the thread, its messages in `seq` order, its turns with status, and its actions. Given an
   unknown id or another admin's thread, then 404.
3. Given `POST /admin/chat/threads` (`create_admin_chat_thread`), when an admin calls it, then a thread row is
   created with the admin id and email and a derived title (CHAT-002's 60-character rule once the first message
   exists).
4. Given `DELETE /admin/chat/threads/{thread_id}` (`delete_admin_chat_thread`), when an admin deletes their thread,
   then the response is 204, the delete is the children-before-parent hard delete audited as `thread_deleted`, and
   a second delete is also 204 with no second audit row. Audit rows and `optimization_runs` rows are untouched.
   This route answers 204 even when `HERMES_CHAT_ENABLED` is false (D-4).

Resume:

5. Given `GET /admin/chat/turns/{turn_id}/stream` (`resume_admin_chat_turn`, SSE), when the turn is `running` in
   this API process, then it first replays the assistant text already stored for the turn and then forwards new
   deltas until the terminal frame. Given the turn is not running here (another process, or a restart), then it
   sends the stored text and one terminal frame carrying the stored status.
6. Given an unknown `turn_id` or one belonging to another admin, then 404.
7. Given the first token of a turn, when it arrives, then the stored assistant text is updated so a resume has
   something to replay (this is the write CHAT-003's completion path already needs; assert it happens
   incrementally, not only at the end).

Cancel:

8. Given a browser abort on `POST /admin/chat/completions`, when the ASGI receive channel reports a disconnect,
   then the turn is marked `cancelled`, `turn_cancelled` is audited, the upstream Hermes request is closed, and
   the partial assistant text is kept. `cancel_optimization_run` is not called.
9. Given the same abort, when a linked optimization run exists for the thread, then the run row is unchanged.

Restart:

10. Given the API restarts with a turn left `running`, when the lifespan runs, then `fail_interrupted_chat_turns`
    (called next to `recover_running_runs` in `app.py`) marks it `failed` with
    "Interrupted: the API restarted while this reply was in progress.", and a later resume returns that status.
11. Given `recover_running_runs` and `fail_interrupted_chat_turns` both run at startup, when an
    `optimization_runs` row is mid-run, then chat startup does not change it.

Retry:

12. Given a request with `retry_of=<turn_id>`, when the id is a turn in the same thread, then a new turn is stored
    with `retry_of` set and a fresh reply streams. Given the id is not in that thread, then 400. The thread shows
    both turns.

Performance:

13. Given `EXPLAIN QUERY PLAN` for the messages-by-thread and threads-by-admin queries, when a test runs it on the
    schema from CHAT-002, then each reports an index search (`USING INDEX`) and not a full scan.

Tests:

14. Given the suite, when it runs, then it covers: reload mid-stream yields the stored partial text and then the
    live tail; an abort during streaming yields `cancelled` plus the partial text; a simulated restart marks the
    turn `failed` and a later resume returns that status; another admin's thread id is 404; deleting a thread
    removes its messages and leaves audit rows; a retry with a foreign `retry_of` is 400.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_threads.py
uv run --env-file .env pytest -v tests/test_admin_chat_routes.py -k "resume or cancel or retry or delete"
uv run --env-file .env pytest -v tests/test_api_optimization.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck
```

## Out of scope
- Rate limiting and the audit read (CHAT-006).
- Action cards and their confirm path (CHAT-007).
- The run panel data (CHAT-008).
- The browser's own abort and resume wiring (CHAT-009, CHAT-010). This story proves the server half against a
  real local HTTP client that closes the connection.
- Any Hermes-side session: no `X-Hermes-Session-Id`, no `hermes_session_id` column.

## Notes
- The live-tail half of resume needs an in-process registry of running turns (a `dict[turn_id, asyncio.Queue]`
  on `app.state`). A4 in the PRD assumes one uvicorn worker; AC 5's fallback is exactly what makes the multi-worker
  case degrade rather than break. Do not add Redis for this.
- Register the queue when the completions route starts a turn (CHAT-003) and remove it in a `finally`, so a
  crashed relay cannot leak an entry.
- A dropped connection is a normal end (NFR-1), not an error: log one structured line, do not let the exception
  surface in the API log.
- The abort hook in CHAT-003 closes the upstream request; this story is what writes `cancelled`. Keep the two in
  one place in the module so a future edit cannot do one without the other.
- Delete stays reachable with chat off. The chat router's exempt-path constants from CHAT-001 are the single
  source for that list; CHAT-012 checks it on the deployment.
- For AC 13, the two queries are the ones `get_thread` and `list_threads` issue. Test through
  `conn.execute("EXPLAIN QUERY PLAN ...")` on the same SQL strings the module uses — extract them as module
  constants so the test cannot drift from the query.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; a real local HTTP server, no mocks
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated if any route list is documented
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, and `bun run typecheck` pass
- [ ] Committed on its own before CHAT-006 starts

# ASKCHAT-001 — Keep conversations on the server, private to their owner

## Type
Feature

## Status
Draft

## Summary
A member's Ask conversations stop being a browser artefact and become a server object. Reloading, signing in from another browser, or reopening the app next week shows the same threads. Every route is scoped to the member who owns it, and deleting a conversation removes the conversation — not the votes that were cast in it.

## PRD traceability
- Related: FR-010, FR-017, FR-019 (the schema half)
- Decisions: D-5 (server-side threads, per member), D-9 (a delete keeps `interactions` and votes), D-11 (unbounded, 50-item list cap, no prune), D-3 (the `Grounding` enum is consumed, not widened)
- Journeys: J1 step 1, J2 step 4
- NFR touchpoints: NFR-1 (no row stays `running`), NFR-4 (per-owner scoping), NFR-5 (privacy), NFR-2 (no new service)

## Dependencies
- Requires: nothing. The store is additive, and the ASK- work it consumes (`Grounding`, `rerank.min_relevance_score`) is already on the branch at `223a122`.
- Blocks: ASKCHAT-003, ASKCHAT-004, ASKCHAT-005, ASKCHAT-006.

## Acceptance criteria

**The store**

1. Given `src/deepfix_rag/ask_chat.py`, when it is read, then it owns the schema and every read and write for threads and turns, using `deepfix_rag.storage.connect` and `settings.product_db_path` — the same database the API already uses — and exposes `init_ask_chat(db_path)`, called from the API lifespan next to `init_admin_chat` in `src/deepfix_rag/api/app.py`.
2. Given a fresh database, when `init_ask_chat` runs twice, then both runs succeed without error, `ask_chat_threads` and `ask_chat_turns` exist with the columns D-5 lists, and the second run neither adds nor drops a table or a row.
3. Given `ask_chat_turns.status`, when an unknown value is written, then the `CHECK` constraint rejects it. The accepted values are exactly `running`, `completed`, `failed`, `cancelled`.
4. Given the two reads a thread load makes, when the indexes are inspected, then `ask_chat_threads (owner_id, updated_at DESC)` and `ask_chat_turns (thread_id, seq)` exist, and `EXPLAIN QUERY PLAN` for the threads-by-owner query and the turns-by-thread query each report an index search rather than a full scan.
5. Given `create_thread`, `list_threads(owner_id, limit=50)`, `get_thread`, `rename_thread(title 1..120)`, `delete_thread`, `append_turn`, `open_turn`, `finish_turn`, `set_grounding`, and `count_recent_turns`, when their signatures are read, then every one is fully typed and every one that reaches stored data takes `owner_id` and filters on it.
6. Given a rename with a title of 0 characters or 121 characters, when it is called, then it is refused and the stored title is unchanged. Given 1 character or 120, it is accepted.

**The columns the votes depend on**

7. Given an existing `interactions` table, when `init_ask_chat` runs, then `interactions` gains nullable `thread_id` and `turn_id` columns via `ALTER TABLE … ADD COLUMN` guarded by a `PRAGMA table_info` check, exactly as `user_email` was added, and running the migration twice is harmless.
8. Given the `interactions` table after this story, when `INSERT … kind='ask_chat'` is attempted, then the `CHECK` still rejects it. The permitted kinds remain `retrieval` and `ask`. This is D-5: one row per assistant turn with `kind='ask'`, never a new kind.

**Deleting a conversation keeps the evidence (D-9)**

9. Given a thread with three answered turns and votes on two answers and four passages, when `DELETE /ask/chat/threads/{thread_id}` is called, then the response is 204, the thread row and its `ask_chat_turns` rows are gone, and in one transaction the thread's turns are removed before the thread row.
10. Given the same delete, when the database is inspected afterwards, then every `interactions` row for those turns still exists, so does every `interaction_hits` row, every `feedback_events` row, every `feedback_mirrors` row, and every `feedback_comments` row. The votes are still returned by `feedback.current_votes`, and `feedback.build_questions` still turns them into dev or holdout questions.
11. Given the same delete, when the admin history is read through `GET /admin/interactions`, then the surviving interactions still appear, because the admin view is the feedback record rather than the member's conversation. This is the stated consequence of D-9 and is asserted, not merely allowed.

**No retention window (D-11)**

12. Given a thread whose `updated_at` is `2021-01-01T00:00:00+00:00`, when `GET /ask/chat/threads` and `GET /ask/chat/threads/{thread_id}` are called, then it still appears in the list and still loads with its turns. It is not pruned, hidden, or expired.
13. Given the codebase, when it is searched for a retention setting or a prune job touching `ask_chat_threads` or `ask_chat_turns`, then there is none: no `ASK_CHAT_THREAD_RETENTION_DAYS`, no scheduler hook, no delete-by-age anywhere. The 50 in `list_threads` is a display cap and does not remove anything.
14. Given a member with 51 threads, when `GET /ask/chat/threads` is called, then the 50 most recent are returned and the 51st is still reachable by id.

**The routes**

15. Given the OpenAPI schema, when it is read, then `list_ask_chat_threads` (`GET /ask/chat/threads`), `create_ask_chat_thread` (`POST /ask/chat/threads`, 201), `get_ask_chat_thread` (`GET /ask/chat/threads/{thread_id}`), `rename_ask_chat_thread` (`PATCH /ask/chat/threads/{thread_id}`), and `delete_ask_chat_thread` (`DELETE /ask/chat/threads/{thread_id}`, 204) all exist with those `operation_id`s and the shared `error_responses(...)` entries.
16. Given a caller with no valid identity token, when any of the five routes is called, then it answers 401.
17. Given member A and member B, when B calls `GET`, `PATCH`, or `DELETE` on A's thread id, then every one answers 404 — not 403 — so the existence of another member's thread is not leaked. Given B calls `GET` on A's turn id through any route, the same 404 applies.
18. Given `GET /ask/chat/threads/{thread_id}`, when it returns, then it carries the thread, its turns ordered by `seq`, and each turn's `grounding` and `citations` fields, so a reload can draw the badges without a second request per turn.
19. Given `web/openapi.json` and `web/src/client`, when `npm run generate:api` runs, then the committed files match what it writes.

**A restart leaves no stuck turn**

20. Given the API restarts while a turn is `running`, when the lifespan runs, then `fail_interrupted_ask_turns(db_path)` — called next to `fail_interrupted_chat_turns` — settles it `failed` with a readable reason and an `ended_at`, and a later read of that turn reports `failed`.

**The required test**

21. Given `tests/test_ask_chat_threads.py`, when it runs against a real SQLite file through the real app, then it covers, in one end-to-end pass: create, list ordering, rename, get, delete; a 404 on every route for the other member's id; a deleted thread leaving `interactions`, `interaction_hits`, `feedback_events`, and `feedback_mirrors` intact with a surviving vote still returned by `current_votes` and still exported by `build_questions`; a restart between two requests settling the `running` turn `failed`; the `2021-01-01T00:00:00+00:00` thread still listing and loading; and the two `EXPLAIN QUERY PLAN` assertions. No `unittest.mock`; the app is built through `create_app` against `tmp_path`.

## Out of scope
- The streaming route, the tool, and any model call. This story stores conversations and serves them; ASKCHAT-003 produces their content.
- Any change to `POST /ask`, `POST /retrieval`, or `GET /interactions/{id}`, beyond the two new nullable columns.
- Importing the browser's existing localStorage sessions. That is ASKCHAT-009.
- Sharing a thread, exporting it, or making it public.
- Pagination beyond the 50-item list. D-11 settles that there is none.

## Notes
- The reason the delete does not cascade into `interactions`: those rows are the optimizer's dataset. `build_questions` reads `feedback_events` joined to `interactions` and `is_holdout` hashes the normalized query text, so a cascade would let a member change what the optimizer is scored on by tidying their history. D-9 records the decision and its consequence.
- `ask_chat_turns.citations` is stored as JSON text with the shape `[{marker, rank, doc_id}]`. ASKCHAT-003 validates the markers; this story only stores and returns them.
- `grounding` is stored as the landed `Grounding` enum's string value (`grounded`, `declined`, `no_relevant_sources`). Do not add a fourth value: ASK-001 owns that enum and `POST /ask` already returns it.
- The migration for `interactions` follows `user_email`'s pattern in `src/deepfix_rag/interactions.py` — `PRAGMA table_info`, then `ALTER TABLE … ADD COLUMN` only when the column is absent — because a `CHECK` cannot be altered in place and a table rebuild is exactly what D-5 avoids.
- One uvicorn worker is assumed throughout, as the admin chat does. Nothing here depends on process affinity, so a second worker would be a performance question rather than a correctness one.

## Definition of done
- [ ] Code complete
- [ ] `tests/test_ask_chat_threads.py` green against a real SQLite file and the real app, no mocks
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run --env-file .env pytest -m "not network and not e2e"` pass
- [ ] Committed on its own before ASKCHAT-002 starts

# CHAT-002 — Chat store and audit tables

## Type
Feature

## Status
Draft

## Summary
A developer gets one domain module that owns the chat's SQLite schema and every read and write against it, so the
HTTP routes added later stay thin adaptors. It also owns the audit trail, the redaction helper, and the hard
delete the admin's Delete button will call.

## PRD traceability
- Related: FR-010, FR-016, FR-018, FR-021
- Decisions: D-4 (hard delete, audit rows survive without message content)
- Journeys: J4 (thread delete and surviving audit rows)
- NFR touchpoints: NFR-5 (conversation text stays in SQLite, never in S3 or MLflow), NFR-1 (no `running` row
  left by a restart), NFR-2 (audit and transcript data are small)

## Dependencies
- Requires: CHAT-001 (settings exist, and the chat key can be redacted once it is in `_SECRET_ENVS`).
- Blocks: CHAT-003, CHAT-004, CHAT-005, CHAT-006, CHAT-007, CHAT-008.

## Acceptance criteria

Schema:

1. Given an empty database, when `init_admin_chat(db_path)` runs, then it creates the six tables with exactly
   these columns and no change to `optimization_runs`:

   | Table | Columns |
   | :--- | :--- |
   | `admin_chat_threads` | `thread_id` PK, `title`, `created_by`, `created_by_email`, `created_at`, `updated_at` |
   | `admin_chat_turns` | `turn_id` PK, `thread_id`, `admin_id`, `admin_email`, `status`, `retry_of`, `error`, `request_chars`, `first_token_ms`, `started_at`, `ended_at` |
   | `admin_chat_messages` | `message_id` PK, `thread_id`, `turn_id`, `role`, `seq` INTEGER, `content`, `created_at` |
   | `admin_chat_actions` | `action_id` PK, `thread_id`, `turn_id`, `kind`, `payload`, `status`, `created_at`, `expires_at`, `decided_at`, `decided_by`, `result` |
   | `admin_chat_thread_runs` | `thread_id`, `run_id`, `linked_at`; PK `(thread_id, run_id)` |
   | `admin_chat_audit` | `event_id` INTEGER PK AUTOINCREMENT, `at`, `admin_id`, `admin_email`, `thread_id`, `turn_id`, `kind`, `detail` |

2. Given the tables, when the schema is inspected, then `admin_chat_turns.status` accepts exactly
   `running|completed|failed|cancelled`, `admin_chat_actions.status` accepts exactly
   `proposed|confirmed|rejected|expired|failed`, and `admin_chat_audit.kind` accepts exactly `turn_started`,
   `turn_completed`, `turn_cancelled`, `turn_failed`, `rate_limited`, `action_proposed`, `action_confirmed`,
   `action_rejected`, `action_expired`, `thread_deleted`. Each is enforced by a `CHECK`; inserting an unknown
   value raises `sqlite3.IntegrityError`.
3. Given `init_admin_chat` runs twice on the same file, then it is idempotent and creates the five indexes
   `admin_chat_threads (created_by, updated_at DESC)`, `admin_chat_messages (thread_id, seq)`,
   `admin_chat_turns (thread_id, started_at)`, `admin_chat_turns (admin_id, started_at)`, and
   `admin_chat_actions (thread_id, status)` with `CREATE INDEX IF NOT EXISTS`.
4. Given the schema, when foreign keys are listed with `PRAGMA foreign_key_list`, then no foreign key references
   `admin_chat_threads` from `admin_chat_audit`, and `admin_chat_thread_runs.run_id` has no foreign key to
   `optimization_runs`.

API surface:

5. Given the module, when its public names are listed, then these typed functions exist:
   `init_admin_chat`, `create_thread`, `list_threads`, `get_thread`, `delete_thread`, `append_message`,
   `update_assistant_text`, `start_turn`, `finish_turn`, `thread_runs`, `record_audit`, `count_recent_turns`,
   `redact`. Every signature is fully annotated, uses `Path` for paths, and uses `X | None` rather than
   `Optional`.
6. Given a thread created by admin A, when admin B calls `get_thread` or `list_threads` for it, then
   `get_thread` raises `KeyError` and `list_threads("B")` does not contain it. The same holds for the thread's
   turns and messages.
7. Given a thread with no title, when it is created, then `title` is the first 60 characters of the first user
   message with whitespace collapsed, and no model call is made.
8. Given `list_threads(db_path, admin_id, limit=50)`, when rows are returned, then they are ordered by
   `updated_at` descending and capped at `limit`.

Delete (D-4):

9. Given a thread with turns, messages, action cards, and a linked run, when `delete_thread` runs, then in one
    transaction it writes one `thread_deleted` audit row (with the thread id and deleted counts in `detail`),
    then deletes the thread's action cards, then its messages, then its turns, then its `admin_chat_thread_runs`
    rows, then the thread row. `admin_chat_audit` rows and `optimization_runs` rows are untouched.
10. Given a thread already deleted, when `delete_thread` runs again, then it returns without raising, the second
    delete is a no-op, and exactly one `thread_deleted` audit row exists for that thread.

Audit and redaction:

11. Given `record_audit(db_path, ..., kind="turn_started", detail="...")`, when the detail exceeds 500
    characters or contains a secret, then the stored `detail` is redacted with the chat key included and
    truncated to `STEP_PREVIEW_LIMIT` (500).
12. Given a `HERMES_CHAT_API_SERVER_KEY` value, when it is added to `_SECRET_ENVS` in
    `src/deepfix_rag/optimization.py` and passed through `redact`, then the output contains `[redacted]` and no
    substring of the key. `redact` also removes paths under `PRODUCT_DATA_DIR`.
13. Given a turn left `running`, when `finish_turn(..., status="failed")` and the interruption helper
    `fail_interrupted_chat_turns(db_path)` run, then the row stores `INTERRUPTED` wording and never remains
    `running`. `fail_interrupted_chat_turns` is exported here and called from the API lifespan in CHAT-005.

Tests:

14. Given `tmp_path` and a real SQLite file (no mocks), when the test module runs, then it covers: create / list
    / get / delete isolation between two admin ids; an unknown audit `kind` rejected; `expires_at` round-trip;
    `updated_at` descending order; a delete removing turns, messages, actions, and thread-run links while
    leaving audit rows and the `optimization_runs` row; a second delete writing no new `thread_deleted` row; and
    the two `PRAGMA` schema checks from AC 4.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat.py
uv run --env-file .env pytest -v tests/test_optimization.py -k "redact or secret"
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check
```

Mark the new module `pytestmark = [pytest.mark.unit, pytest.mark.integration]`, matching
`tests/test_optimization.py`'s use of a real temp SQLite file.

## Out of scope
- Any HTTP route (CHAT-003, CHAT-005, CHAT-006, CHAT-007, CHAT-008).
- The action-card parser and its status transitions beyond storing `payload` and `expires_at` (CHAT-007).
- Rate-limit decisions (CHAT-006); this story only supplies `count_recent_turns`.
- Retention or purging of audit and transcript rows (open question, not this release).
- MLflow tracing of a chat turn (CHAT-011).

## Notes
- Use `deepfix_rag.storage.connect` and the same file as `optimization_runs`
  (`settings.product_db_path` = `PRODUCT_DATA_DIR/product.db`). `connect` sets
  `PRAGMA foreign_keys = ON`; AC 4 is the reason the audit table declares none.
- Call `init_admin_chat` from the API lifespan in `src/deepfix_rag/api/app.py` next to `init_optimization`.
- `_redact` and `_secrets` in `optimization.py` are private today. Expose them through a narrow public
  `redact(text, ...)` in `admin_chat` (or a small shared helper) rather than importing a private name into a new
  module at many call sites; keep `_SECRET_ENVS` in `optimization.py` as the single list.
- `INTERRUPTED` wording: "Interrupted: the API restarted while this reply was in progress." Define it once as a
  module constant and reuse it in CHAT-005.
- `count_recent_turns(db_path, admin_id, window_seconds)` reads `admin_chat_turns` only. CHAT-006 depends on this:
  audit writes must never inflate the limiter's count.
- Thread titles are derived, never model-generated. Keep the derivation pure so its test needs no database write
  beyond the fixture.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green; no `unittest.mock`
- [ ] Docs updated if the schema is described in any doc (`tasks/prd-admin-hermes-chat.md` already is; do not edit
      the PRD)
- [ ] `uv run ruff check`, `uv run ruff format --check`, and the fast pytest suite pass
- [ ] Committed on its own before CHAT-003 starts

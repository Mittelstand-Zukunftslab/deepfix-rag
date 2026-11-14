# CHAT-007 — Action cards

## Type
Feature

## Status
Draft

## Summary
The assistant can hand the admin a card that starts or cancels an optimization run, and only the admin's own
confirm click executes it — through the same domain functions the Optimization screen calls. Chat never holds the
power to start, cancel, or promote anything by itself.

## PRD traceability
- Related: FR-013, FR-014, FR-015
- Decisions: none directly; implements the PRD's "How the assistant proposes an action" and open question 3's
  recommended default (a card is confirmable while its turn still streams)
- Journeys: J2 steps 1–3 (propose, confirm, run panel appears)
- NFR touchpoints: NFR-2 (a confirmed start obeys `RunInProgress`), NFR-4 (admin on confirm and reject), NFR-8
  (a refused action is one structured log line)

## Dependencies
- Requires: CHAT-002 (actions table, the audit `CHECK` list with `action_*` kinds), CHAT-003 (the turn whose reply
  is scanned and the terminal frame that carries `actions`), CHAT-006 (the audit read an admin uses to see who
  confirmed what, and the `record_audit` redaction path a confirm shares).
- Blocks: CHAT-008 (the confirmed run is what the panel shows), CHAT-010 (the card UI), CHAT-012 (J2 on the
  deployment).

## Acceptance criteria

Parsing:

1. Given a completed assistant reply, when it is scanned, then it looks for fenced blocks whose info string is
   exactly `deepfix-action` containing one JSON object with `kind` and `reason`. The recognised `kind` values are
   exactly `start_optimization_run` and `cancel_optimization_run`.
2. Given a reply with more than two valid blocks, then at most two cards are stored and the rest are dropped.
   Given an unknown `kind`, a malformed block, or a dropped extra, then it produces no card and is counted in the
   turn's audit `detail`; it is never an error the admin must handle.
3. Given a `cancel_optimization_run` block carrying a `run_id`, when that id is not a row in `optimization_runs`,
   then the block is dropped and counted. Given a valid `run_id`, it is stored in `payload`.
4. Given a recognised block, when it is stored, then the row has `status: "proposed"`, `expires_at` = now +
   `ADMIN_CHAT_ACTION_TTL_SECONDS` (600), and `action_proposed` is audited.

Confirm and reject:

5. Given `POST /admin/chat/actions/{action_id}/confirm` (`operation_id="confirm_admin_chat_action"`), when the
   caller is an admin who owns the thread, the row is `proposed`, and it is unexpired, then the action runs.
   `start_optimization_run` and `cancel_optimization_run` are reached through the same shared helper
   `POST /admin/optimization/run` and `POST /admin/optimization/runs/{run_id}/cancel` use (extracted in this story
   as `start_run_for(request, trigger="manual")` and `cancel_run_for(request, run_id)`), so the chat path cannot
   drift from the Optimization screen. The response body is the existing `OptimizationRunResponse`.
6. Given the confirm, when the domain function raises, then `RunInProgress` maps to 409, `AgentUnavailable` to
   503, `KeyError` to 404, and `RunNotRunning` to 409, exactly as the existing optimization routes do, with the
   same messages.
7. Given a second confirm on the same action, then 409. Given an expired row, then it is marked `expired` on read,
   `action_expired` is audited, and the response is 409. Given another admin's action, then 404.
8. Given `POST /admin/chat/actions/{action_id}/reject` (`operation_id="reject_admin_chat_action"`), then the row
   becomes `rejected` and `action_rejected` is audited. Given an already decided row, then 409.
9. Given a confirm that succeeds, then `action_confirmed` is audited with the resulting `run_id` in `detail`.
   Confirm and reject are the only chat routes that can change run state; no other chat path writes run state.
10. Given a card whose thread's turn is still `running`, when the admin confirms it, then the run is created and
    the in-flight stream is unaffected (the conversation plane and the job plane are separate).

Safety invariants:

11. Given every public callable in `admin_chat`, when a test enumerates them, then none imports or calls
    `apply_candidate`, writes `configs/champion.yaml`, or reads holdout (`feedback.is_holdout`,
    `_holdout_records`). The test walks function source and module attributes rather than relying on review.
12. Given the confirm route, when `start_optimization_run` is reached, then it goes through the same shared helper
    the Optimization route uses (`start_run_for(request, trigger="manual")`, extracted from
    `start_optimization_run_route`), with the same `options=settings_from(settings)` and the same coordinator. The
    test asserts it behaviourally, not with a spy: both routes are hit against the same real database with too few
    labelled questions, and both return the same skip status and reason. A source assertion additionally pins that
    neither route calls `start_optimization_run` directly.
13. Given a reply that never emits a block, then nothing is lost: the terminal frame carries `"actions": []` and
    the Optimization screen is unchanged.

Tests:

14. Given the suite, when it runs, then it covers: one valid block yields one card; an unknown `kind` yields none
    and is counted; two admins cannot confirm each other's card (404); an expired card is 409 and audited
    `action_expired`; a double confirm is 409; a confirm of a start while a run is already running is 409 with the
    existing message; a confirm during an open stream creates the run and the stream still completes.

## Test commands

```bash
uv run --env-file .env pytest -v tests/test_admin_chat_actions.py
uv run --env-file .env pytest -v tests/test_api_optimization.py
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run ruff check
uv run ruff format --check

cd web && npm run generate:api && bun run typecheck
```

`tests/test_api_optimization.py` is the reference for the domain-call parity in AC 12 and the 409/503 mappings in
AC 6; extend it only if a shared helper is extracted, otherwise assert from the chat test.

## Out of scope
- Any new action kind. The allowlist is two and stays two; promotion is not an action at all (FR-015).
- The card's visual design and the confirm dialog copy (CHAT-010).
- Linking the run to the thread. `admin_chat_thread_runs` and its route are CHAT-008's; until it lands the thread
  shows the run from the confirm response only, and CHAT-008 wires the link.
- Editing, replaying, or deleting a run (explicitly out of scope in the PRD).

## Notes
- Put the parser in `admin_chat` next to the store, not in the route: it must be unit-testable against fixture
  strings with no HTTP and no upstream server.
- Parse only after the turn is `completed`, from the stored text (not from the streamed deltas), so a cancelled or
  failed turn proposes nothing. State that in a test.
- The policy text in CHAT-004 tells the model this block shape. Any change here changes that text in the same
  commit; a test can assert the two agree by parsing the example block quoted in the prompt.
- `ADMIN_CHAT_ACTION_TTL_SECONDS` expiry is evaluated on read (confirm/reject), never by a background job, so an
  API that was down cannot leave a card confirmable past its expiry.
- `expires_at` comparisons use the same time source as the limiter's `now` so tests can move the clock.

## Definition of done
- [ ] Code complete
- [ ] Tests added and green, including the allowlist and the no-promotion walk
- [ ] `web/openapi.json` and the generated client regenerated and committed
- [ ] Docs updated if the action kinds are listed anywhere user-visible
- [ ] `uv run ruff check`, `uv run ruff format --check`, the fast pytest suite, and `bun run typecheck` pass
- [ ] Committed on its own before CHAT-008 starts

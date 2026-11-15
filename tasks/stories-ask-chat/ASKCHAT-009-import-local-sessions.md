# ASKCHAT-009 — Bring the browser's existing conversations across, once

## Type
Feature

## Status
Implemented — `2fb98d5` on `feat/ask-chat-streaming`

## Summary
A member who has been using Ask does not lose their history when conversations move to the server. Their existing browser sessions are imported once into server threads, each turn linked to the interaction it already has, and the browser keys are then removed so nothing is duplicated or kept in two places.

## PRD traceability
- Related: FR-033
- Decisions: D-5 (server-side threads; a one-time best-effort import), D-9 (the import must not invent votes or interactions)
- Priority: Should. If it is cut, the fallback is deleting the keys with a one-time notice, and no member is left on a store the server cannot see.

## Dependencies
- Requires: ASKCHAT-001 (threads and turns), ASKCHAT-006 (the client that can call the import).
- Blocks: nothing. It is ordered last among the build stories because it is the only one that can be dropped without weakening a Must requirement.

## Acceptance criteria

**The route**

1. Given `POST /ask/chat/threads/import` with a member's legacy sessions — `{sessions: [{id, title, created_at, updated_at, turns: [{interaction_id, user_query, answer}]}]}` — when it is called, then it creates one thread per session and one turn per stored turn, and answers with the counts created and skipped. **snake_case is canonical**, like every other route; the request model accepts the retired page's camelCase (`interactionId`, `createdAt`, `updatedAt`, `userQuery`) as aliases, so an older browser's payload is still understood. The list is bounded at 200 sessions (a longer payload is a 400 naming the bound), and an empty list is accepted and creates nothing.
2. Given a stored turn with an `interactionId`, when the import runs, then the turn is linked to that interaction only if the interaction exists and `interactions.user_id` is the caller. Otherwise the turn is skipped and counted, and no turn is invented for it.
3. Given a stored turn with no `interactionId`, when the import runs, then it is skipped and counted. The import never fabricates an `interactions` row.
4. Given the same import called twice with the same payload, when the second call runs, then no duplicate thread and no duplicate turn exists: the import is idempotent per session id and per interaction id. Per session id, the thread records the id it came from in `ask_chat_threads.legacy_session_id`, so a second run finds it and creates nothing; per interaction id, a turn already linked to that interaction is not created again.
5. Given two members importing payloads that name overlapping interaction ids, when both run, then each keeps only the interactions it owns and neither sees the other's thread.
6. Given an unauthenticated caller, when the route is called, then 401. Given a session list larger than a sane bound, then 400 with a sentence naming the limit, rather than an unbounded write.

**The client**

7. Given the Ask page on first load and the legacy keys `deepfix.chat_sessions.v2:<ownerId>` and `deepfix.active_session_id.v2:<ownerId>` present, when the page mounts, then it calls the import once, then removes both keys, and never calls it again for that member.
8. Given the import fails — a network error, a 400 — when the page handles it, then the keys are removed anyway and a one-time notice says the old conversations could not be carried over. Leaving a member on a store the server cannot see is worse than telling them once.
9. Given a member whose keys were already removed, when the page loads, then no import request is made at all.

**The required test**

10. Given a real database through the real app, when `tests/test_ask_chat_import.py` runs, then: two members import overlapping interaction ids and each keeps only their own; a re-run is a no-op; a session whose interaction was deleted imports its thread with the turn skipped and the count reported; and a turn with no `interactionId` is counted as skipped rather than stored as an orphan.
11. Given `web/src/test/ask-chat-import.test.ts`, when it runs, then the page's first load with both keys present issues exactly one import request and then removes the keys, and a second render issues none.

## Out of scope
- Importing a session for a member who never signs in again. The import runs on first load, not as a background job.
- Reconstructing passages, grounding, or citations for an imported turn. The interaction already holds its hits; the page reads them through the existing routes. Only the answer text and the link to the interaction travel.
- Merging two browsers' sessions into one thread. One session becomes one thread.
- Any migration of the admin chat, which never used localStorage.

## Notes
- `ChatTurn.interactionId` is already written by the current page, which is what makes this import possible at all. A turn without it cannot be linked, because the interaction is the only place its hits and votes live; inventing one would create a question the optimizer would later score with no passages.
- Idempotence is per session id and per interaction id, not per payload hash: a member may load two browsers, and the second import must not duplicate the first's threads.
- Removing the keys even on failure is a deliberate choice. A retry loop against a store the server cannot read would keep the same conversations alive in two systems with different contents, and the member would see them change under them.
- The import is the one place the server reads a client-supplied list of interaction ids. AC 2's ownership check is therefore the security boundary: without it a member could attach another member's interactions to their own thread.

## Definition of done
- [ ] Code complete
- [ ] `tests/test_ask_chat_import.py` and `web/src/test/ask-chat-import.test.ts` green
- [ ] The generated client regenerated for the new route and committed
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run --env-file .env pytest -m "not network and not e2e"`, and `bun run typecheck` / `bun run test` pass
- [ ] Committed on its own before ASKCHAT-010 starts

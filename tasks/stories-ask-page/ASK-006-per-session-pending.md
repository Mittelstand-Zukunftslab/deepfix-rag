# ASK-006 — Keep pending state with the session that asked

## Type
Bug

## Status
Draft

## Summary
A member who asks a question and switches to another chat, or starts a new one, sees the answer and
the thinking card land in the session that asked. The other session is not blocked: it keeps its own
composer, and its own asking state, so a second question in a second session is possible.

## PRD traceability
- Related: FR-022 (the Ask screen), US-010
- Decisions: one in-flight request per session; the composer is disabled only in the asking session
- Out-of-PRD: none. This closes the review's "pending state is page-level, not per session".

## Dependencies
- Requires: nothing. It moves state inside `chat-page.tsx` only.
- Blocks: nothing.

## Acceptance criteria

1. Given the Ask page, when it is read, then pending state is keyed by session id — a map or an
   equivalent per-session record — and there is no module-level or component-level boolean that says
   "the page is asking".
2. Given session A is asking a question and the member switches to session B, when session B renders,
   then no thinking card and no pending query appear in B, and B's composer is enabled.
3. Given session A is asking and the member opens New Chat, when the new session renders, then it is
   empty with an enabled composer, and A still shows its thinking card when the member returns to it.
4. Given session A is asking and the member returns to A, when A renders, then its thinking card with
   the query it asked is still there, and the composer in A is disabled with its busy state.
5. Given the request started in session A finishes while the member is viewing session B, when the
   response arrives, then the turn is appended to session A, A keeps its own title update, and B is
   untouched.
6. Given the member asks in session B while A is still asking, when both settle, then each session
   holds its own turn and neither answer appears in the other session.
7. Given a request fails in session A, when the member is viewing B, then the failure is recorded as a
   turn in A and B shows no error.
8. Given `web/src/test/pages.test.tsx` and any other existing Ask test, when they run, then they pass
   with their assertions unchanged apart from where they drive a per-session control.
9. Given session A is asking, when the member deletes session A, then A's pending entry is dropped and
   a response that arrives afterwards does not recreate the session or write its turn anywhere.
10. Given the Assistant UI runtime, when the active session is not the asking one, then
    `useExternalStoreRuntime` reports `isRunning` false, so no assistant placeholder is drawn in a
    session that did not ask.

## Out of scope
- Cancelling an in-flight request. `askService` has no abort path and the page has no Stop control;
  adding one is a separate outcome.
- Persisting pending state across a reload. A reload loses an in-flight question today and still does.
- The admin Hermes chat's streaming state. It already owns per-thread runtimes and is not touched.

## Notes
- The completion handler currently closes over `activeSession.id`. It must close over the session id
  captured when the request started, not the current selection, or AC 5 fails while AC 2 passes.
- A parallel ask in a second session needs no queue or lock: `POST /ask` is stateless and the two
  requests do not share a backend resource. The story allows it for that reason.
- The thinking card renders from the active session's pending entry, so it appears only there by
  construction rather than by a conditional in the JSX.
- `useExternalStoreRuntime` takes `isRunning`; it must report the active session's pending state so the
  Assistant UI runtime does not draw a second assistant placeholder in a session that is not asking.

## Definition of done
- [ ] Code complete
- [ ] Test proves the mid-request switch, the answer's landing session, and a parallel ask
- [ ] `bun run typecheck`, `bun run lint`, `bun run test` green
- [ ] Screenshots of pending-in-one-session-while-viewing-another captured against a mocked backend

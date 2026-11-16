# ASKCHAT-004 — Stop, resume, regenerate, and fail honestly

## Type
Feature

## Status
Implemented — `98eddf0`, revised by `3b5d24f` on `feat/ask-chat-streaming`

## Summary
Stop ends an answer without losing it. A reload mid-answer resumes the reply in place. Regenerate replaces an answer at the same position instead of stacking a second one under it. A failure says so, offers regenerate, and leaves nothing stuck.

## PRD traceability
- Related: FR-014, FR-015, FR-016, FR-018
- Decisions: D-4 (Stop cancels the stream, keeps the text and the evidence), D-9 (a regenerate clears votes rather than deleting the interaction)
- Journeys: J1 steps 3–4, J3
- NFR touchpoints: NFR-2 (no row left `running`, a dropped connection is a normal end), NFR-4 (another member's turn is 404), NFR-1 (resume replays once)

## Dependencies
- Requires: ASKCHAT-003 (the streaming route, the tool loop, and the `litellm.acompletion` generator).
- Blocks: ASKCHAT-005 (counts turns), ASKCHAT-010 (the trace includes the cancelled path).

## Acceptance criteria

**Stop**

1. Given a turn streaming to a browser, when the member presses **Stop**, then the browser calls `POST /ask/chat/turns/{turn_id}/cancel` and the server cancels the in-flight retrieval and the LiteLLM async generator, so the upstream request is ended rather than left running, and the row settles `cancelled`. A **closed connection on its own does not do this**: that is also what a reload does, and a reload must let the answer finish (FR-016).
2. Given that same abort, when the database is read, then the turn's status is `cancelled` with an `ended_at`, the assistant text written so far is stored, and any tool results already received are stored as that turn's `interaction_hits` so the sources panel survives the stop. A turn that had already searched also records `retrieval_query` — the standalone query its last search ran — and a `grounding` verdict, so what it found is readable when the member comes back rather than looking like a turn that never retrieved.
3. Given that same abort, when the stream has any remaining ability to write, then a terminal `data-deepfix-turn` with `status: "cancelled"` is emitted before the stream closes, so a later resume and the browser agree on the status.
4. Given a stopped turn, when `POST /feedback` is called on its answer or on one of its passages, then the vote is accepted — a stopped answer is still votable, because it is a thing the member read.
5. Given a stopped turn, when the log is read, then the cancel produced one structured line and no exception traceback: a cancelled turn is a normal end, not an error.

**Resume**

6. Given `GET /ask/chat/turns/{turn_id}/stream`, when the turn is `running` in this API process, then it replays the frames that turn has already published — including its `text-start` / `text-delta`* / `text-end` group — and then follows the same turn until its terminal frame, so the answer appears exactly once and none of it is missed. The replay and the tail are taken together under one lock, so a frame is in exactly one of them. A turn whose process is gone degrades to the stored text instead.
7. Given the same route, when the turn is not running here — finished, or a restart, or another worker — then it sends the stored text once and one terminal `data-deepfix-turn` carrying the stored status and grounding.
8. Given an unknown `turn_id`, or one belonging to another member, when the route is called, then 404.
9. Given a resume in flight, when the browser connects, then the route answers with the same `x-vercel-ai-ui-message-stream: v1` stream as the completions route, so the client's `reconnectToStream` needs no second parser.

**Regenerate**

10. Given a thread whose newest turn is `completed` or `cancelled`, when a request arrives with `regenerate: true`, then that turn's answer is replaced in place and its `turn_id` is reused, so the thread does not grow a second answer beside the first.
11. Given the same regenerate, when the thread is re-read, then it contains exactly one assistant turn for that position, not two, and the previous answer's text is nowhere in it.
12. Given votes on the replaced answer or its passages, when the regenerate completes, then each is cleared through the existing `clear` action in `feedback.record_vote`, which also deletes the mirrored MLflow assessment. No new vote action and no `feedback_events.action` `CHECK` change. The regenerate rewrites the turn's **existing** `interactions` row rather than inserting a second, so one assistant turn still has exactly one row and the thread's join stays one-to-one.
13. Given a regenerate request whose newest turn is `running`, when it arrives, then it is refused with **409** rather than replacing a reply that is still being written. The check is on the newest turn in the store, because the AI SDK's reload cannot name a turn id. A `failed` turn is a **valid** target — FR-018 offers regenerate for it — so the replaceable set is `completed`, `failed`, `cancelled`, and only `running` is refused.
14. Given a thread whose newest turn is `no_relevant_sources`, when the browser renders it, then it offers no regenerate control. The refusal is the product's answer, not a transient failure.

**Failure**

15. Given a model failure mid-stream, when it happens, then the stream emits an `error` part with a sentence that names no credential and no private path, then a terminal `data-deepfix-turn` with `status: "failed"`.
16. Given that failed turn, when the database is read, then the status is `failed`, `error` holds the sentence, and no `running` row remains.
17. Given a failed turn, when the browser renders it, then regenerate is offered and pressing it produces a fresh attempt at the same position.

**The required test**

18. Given the real fake OpenAI-compatible endpoint and the real app, when `tests/test_ask_chat_stop_resume.py` runs, then it covers: a client that closes the socket mid-stream **does not** cancel the turn — it runs on and settles `completed` with the whole answer, because a closed socket is a reload and a reload must not lose the reply — and a resume of that turn returns the stored text once and then the terminal frame; an explicit `POST /ask/chat/turns/{turn_id}/cancel` leaves a `cancelled` row with the partial text and the tool's hits, and the fake server records that its response stream was **closed** rather than merely abandoned; a regenerate leaves one assistant message and one `clear` event per prior vote; a failure frame yields `failed` and a regenerate that follows succeeds; and a failed turn reports the deployment's configured cutoff rather than a placeholder, claims no verdict it never reached, has an `ERROR` trace, and leaves no answerless interaction in the export. No mocks.
18a. Given a `TestClient` cannot abort mid-stream, when the abort case runs, then it runs the real app under `uvicorn` on a loopback port and closes a real `httpx` connection part-way through the answer. That is the only way the `GeneratorExit` path — which is what Starlette does on disconnect — is exercised.

## Out of scope
- Editing an earlier message and re-running from there. Replacing the newest turn is the whole of regenerate in this release.
- Branching a conversation into two threads.
- Cancelling anything other than this turn. A stop never touches a vote, another turn, or any other member's request.
- Persisting an in-flight stream across an API restart. The resume route reports the stored status instead, as AC 7 says.

## Notes
- **A disconnect is a `GeneratorExit`, not an exception.** Starlette closes the relay generator when the browser goes, so `except Exception` never sees it. The terminal state is therefore written in a `finally`, and the cancelled path is caught explicitly — the first implementation used `except Exception` alone and left the row `running`, which only a real socket could reveal.
- The abort path is one function, not two. Closing the upstream generator and writing the `cancelled` row belong together in the module so a later edit cannot do one without the other.
- `src/deepfix_rag/api/chat_turns.py` already implements the live-registry plus stored-text-fallback shape for the admin chat: a `dict[turn_id, LiveTurn]` with subscriber queues, published to from the relay and closed in the same `finally` that settles the row. Reuse that pattern rather than inventing a second registry; one uvicorn worker is the stated assumption, and the stored-text fallback is what makes a second worker degrade instead of break.
- Regenerate's "newest turn" resolution belongs on the server, because the AI SDK's reload drops the message it replaces and the browser cannot reliably name it. Resolve it from the stored thread exactly as the admin chat's `_regenerate_target` does.
- The `cancelled` row must keep its partial text. A stopped answer that loses its words is worse than a failed one, because the member cannot tell what they had.

## Definition of done
- [ ] Code complete
- [ ] The four end-to-end cases in AC 18 green against the real fake endpoint and the real app
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run --env-file .env pytest -m "not network and not e2e"` pass
- [ ] Committed on its own before ASKCHAT-005 starts

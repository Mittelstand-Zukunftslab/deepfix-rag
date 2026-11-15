# ASKCHAT-010 — Trace the tool calls and export the standalone question

## Type
Feature

## Status
Draft

## Summary
The optimizer can see what a chat turn did: one MLflow trace per turn, with a child span for each search and the numbers that made it useful or not. And a follow-up like "and who voted against it?" reaches the dev set as the standalone question the model actually searched for, not as a pronoun sentence that would be scored as a retrieval miss.

## PRD traceability
- Related: FR-020, FR-021, FR-023 (the guard test)
- Decisions: D-6 (served retrieval is scored retrieval), D-9 (votes survive, so the export still sees them), D-3 (grounding and citations are on the trace)
- Journeys: J2 step 3
- NFR touchpoints: NFR-2 (a tracing failure never fails a turn), NFR-5 (no conversation dump into MLflow), NFR-8 (one line per turn)

## Dependencies
- Requires: ASKCHAT-003 (the turn and its tool calls), ASKCHAT-004 (the cancelled and failed paths).
- Blocks: ASKCHAT-011 (the runbook checks a trace).

## Acceptance criteria

**The trace**

1. Given a completed turn, when MLflow is read, then there is one trace in the `deepfix_product` experiment tagged with `interaction_id`, `thread_id`, `turn_id`, and `grounding`, and the tag `interaction_id` equals the stored interaction's `trace_id`.
2. Given a turn with two tool calls, when the trace is read, then it has two child spans, one per call, each carrying the tool's `query`, the passage count, `max_score`, and its duration.
3. Given `src/deepfix_rag/tracing.py::trace_request`, when it is read, then nested spans are supported — a new helper or an optional parent — without changing the shape of the traces `ask` and `retrieval` already write, and a test asserts those two are unchanged.
4. Given a tracing failure — MLflow unreachable, an exception inside a span — when a turn runs, then the turn still completes, the failure is logged once, and no error part reaches the member. This is how `trace_request` already behaves and it must not regress.
5. Given a cancelled or failed turn, when the trace is read, then it exists and carries the terminal status, so a turn that produced nothing is still accounted for.

**What the export asks**

6. Given `feedback.build_questions`, when it is read, then `_latest_events` gains a `LEFT JOIN ask_chat_turns` per interaction and selects `COALESCE(t.retrieval_query, i.query)` as the question text, normalised by the existing `normalize_query`. A single-turn `POST /ask` interaction, which has no `ask_chat_turns` row, keeps its own query and is unaffected.
7. Given a two-turn conversation whose second turn searched for a standalone query, when `build_questions` runs, then the exported question for that turn is the standalone query, and the raw message is still what `interactions.query` stores for the history screen and the answer-vote rows.
8. Given `is_holdout`, when it runs, then it still hashes the normalized question text and still selects 20%. The membership of an unchanged query is identical before and after this story.
9. Given `export_feedback`, when it writes `feedback_dev.jsonl` and `answer_downvotes.jsonl`, then both use the same coalesced question, so a downvote on a follow-up is filed against the question the search actually ran.
10. Given `ask_chat_turns.retrieval_query`, when a turn made more than one tool call, then the stored value is the query of the **first** call — the one the answer's passages came from in the normal case — and the later calls stay in the trace rather than being lost.

**The guard (FR-023)**

11. Given `promotion.apply_candidate`, when this story lands, then it is unchanged, it remains the only writer of `configs/champion.yaml` and `indexes/current`, and a test asserts that no module under the ask-chat path imports it or writes those paths.
12. Given the ask-chat code, when it is searched, then it reads no holdout question, no holdout id, and no holdout document id.

**The required test**

13. Given a real database through the real app and MLflow pointed at a local SQLite tracking store, when `tests/test_ask_chat_traces.py` runs, then: a two-turn conversation whose second tool query is standalone exports two questions, the second equal to that tool query; an `ask` interaction from `POST /ask` with no `retrieval_query` exports its own query; the holdout membership of an unchanged query is identical before and after; and the trace for a turn with two tool calls has two child spans and the same `interaction_id` tag as its row. No mocks.

## Out of scope
- Conversation-level evaluation, a multi-turn metric, or a change to the promotion gate. Those are an explicit non-goal and a follow-up project.
- Tracing the browser, the tool's SQL, or the page's renders.
- Changing what `deepfix eval` scores or how the holdout split is drawn.
- A second experiment. The `deepfix_product` experiment is where `ask` already traces; a chat turn is an `ask`.

## Notes
- `trace_request` sets the experiment, opens one span, sets inputs and outputs, tags `interaction_id`, and returns the trace id or `None`. It is called from the `_store` path the ask route already uses, so this story extends that call rather than adding a second integration.
- The `COALESCE` belongs in the SQL `_latest_events` already runs, because `build_questions` and `_answer_downvotes` both read through it. Putting it in Python would need two edits and would let the two drift.
- `retrieval_query` being the first tool call's query is a deliberate simplification: the exported question is one question, and the first search is the one whose passages the answer was built from. A turn that searched twice is still fully visible in the trace, which is what a reviewer would use to understand it.
- The holdout hash staying on the coalesced text means a follow-up question can be holdout. That is intended: it is a real question the corpus cannot trivially answer, and it is exactly the kind of case the gate should not overfit to.

## Definition of done
- [ ] Code complete
- [ ] `tests/test_ask_chat_traces.py` green against a real database and a local MLflow store
- [ ] `tests/test_evaluation_runner.py` and the existing `feedback` tests still green, proving the `ask` and `retrieval` traces and the export are unchanged for them
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run --env-file .env pytest -m "not network and not e2e"` pass
- [ ] Committed on its own before ASKCHAT-011 starts

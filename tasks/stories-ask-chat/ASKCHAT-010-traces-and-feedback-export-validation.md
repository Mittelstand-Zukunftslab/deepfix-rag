# Story validation: ASKCHAT-010 — Trace the tool calls and export the standalone question

- Story file: `tasks/stories-ask-chat/ASKCHAT-010-traces-and-feedback-export.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. Two outcomes — a trace per turn, and a usable question in the dev set — but they share one subject (what the optimizer can see about a chat turn) and one test file, and both are read from the same stored row. Splitting them would produce two stories that each need the other's fixture.

### Should-fix

Two were found and fixed.

1. **AC 10 was missing.** A turn can make up to three tool calls, so `retrieval_query` needed a stated rule or two implementers would pick differently. Now: the first call's query, because that is the one whose passages the answer was normally built from, and later calls stay in the trace. The trade-off is stated rather than hidden.
2. **AC 9 needed to name both export files.** `export_feedback` writes `feedback_dev.jsonl` **and** `answer_downvotes.jsonl`, and only fixing the first would leave a downvote on a follow-up filed against a pronoun sentence. Both are now required to use the same coalesced question.

### Nice-to-have

- The story does not prescribe how nested spans are expressed (`mlflow.start_span` inside the parent, or an explicit parent argument). That is an MLflow API detail, and AC 3 constrains only the outcome: the `ask` and `retrieval` traces must be unchanged.

## Suggested edits (applied)

- AC 10 added.
- AC 9 names both files.

## PRD alignment

- FRs: FR-020 (the trace and its child spans), FR-021 (the export uses the standalone query), FR-023 (the guard test that the ask-chat path cannot promote).
- Decisions: D-6 (served retrieval is scored retrieval — this is the export half), D-9 (votes survive a thread delete, so the export still sees them), D-3 (grounding and citations are on the trace).
- Gaps: none. FR-022's whole-set property is ASKCHAT-011's, as the PRD's Story to FR map says.

## NFR touchpoints

- **NFR-2** — AC 4, AC 5: a tracing failure never fails a turn, and a cancelled or failed turn still has a trace, so "the turn did nothing" is distinguishable from "the turn was not recorded".
- **NFR-5** — AC 3: the existing traces' shape is unchanged, so no conversation text enters MLflow that did not already.
- **NFR-8** — the trace's tags are what make a member's turn findable from an interaction id, which is the same path a vote's assessment already follows.

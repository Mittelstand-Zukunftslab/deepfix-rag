# Story validation: ASKCHAT-002 — Give the model one read-only tool over the champion index

- Story file: `tasks/stories-ask-chat/ASKCHAT-002-search-documents-tool.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

One was found and fixed; it is recorded because it is the kind of error that would have shipped a misleading test.

**The first draft had the tool apply the floor a second time.** It said the tool would call `RAGWorkflow.split_by_relevance_floor` and that AC 11 would assert the tool returns `[]` while `QueryService.retrieve` still returned its passages. Both are wrong against the landed code: `QueryService.retrieve` → `_search` → `_search_split` already calls `_ranker_split`, so the tool receives survivors only, and there is no state in which the two disagree about the floor. The AC as drafted would have asserted a divergence the design exists to prevent, and `validate-story`'s traceability check is what surfaced it — the FR says "served equals scored" while the AC described the opposite.

Fixed by rewording AC 4, AC 5 and AC 11 to say the tool **reports** the floor's result rather than re-applying it, and by stating the test as an equality: raised floor ⇒ both return `[]`.

### Should-fix

1. **AC 6 needed the reason, not just the rule.** `_ranker_split` returns nodes unchanged when the floor is `<= 0` *or* rerank did not run, and the PRD's ASK-002 records why (fused scores sit near 0.03, a different scale). The story now says that, so a reader cannot "fix" the skipped floor by applying 0.2 to a fused score.
2. **AC 7's clamp bound depended on a helper in a route module.** `displayed_limit` lives in `src/deepfix_rag/api/routes/retrieval.py`, which is a route file the domain layer importing would be odd. The story names it as the definition to reuse rather than mandating an import, leaving the move to the implementer — but the number must come from one place.

### Nice-to-have

- The story could name the `MockEmbedding` scores it relies on. It does not, deliberately: the parity test asserts ids, order, and score equality between two calls, not absolute values, so it stays valid if the fixture changes.

## Suggested edits (applied)

- AC 4, 5, 11 rewritten around "reports, does not re-filter".
- AC 6 gains the fused-score reason.
- Notes say explicitly that this story filters nothing.

## PRD alignment

- FRs: FR-004 (the tool's contract and depth), FR-023 (no write path), FR-024 (served equals scored).
- Decisions: D-2 (one tool, always runs, returns the floor's survivors), D-3 (consumes ASK-001's `Grounding`), D-6 (served retrieval is scored retrieval), D-7 (no model setting — the tool has none).
- Gaps: none. FR-024's guard is this story's parity test; ASKCHAT-010 carries the second guard on the champion writer.

## NFR touchpoints

- **NFR-4** — AC 13: read-only, no path to `configs/champion.yaml`, `indexes/current`, or holdout. This is where FR-023 is easiest to break, because the tool is the component closest to the index.
- **NFR-3** — AC 7, AC 6: the depth is bounded, so a tool call cannot hand the model 50 passages and blow the prompt budget.
- **NFR-1** — AC 3: the lease is released in a `finally`, so a failed tool call cannot pin a build that is being replaced.

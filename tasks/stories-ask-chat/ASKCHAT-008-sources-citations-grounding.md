# ASKCHAT-008 — Show the evidence, follow its citations, and say how grounded it is

## Type
Feature

## Status
Implemented — `838fdbf` on `feat/ask-chat-streaming`

## Summary
The passages behind an answer come from what the tool actually returned, not from parsing the prose. Each one is readable and votable, each `[n]` marker in the answer jumps to the passage it names, and the badge tells the member whether the answer was grounded — with a distinct **Partly grounded** state for a grounded answer that cites nothing (D-10).

## PRD traceability
- Related: FR-007, FR-030, FR-034 (the badge half)
- Decisions: D-3 (grounding plus validated citations), D-10 (a grounded but uncited answer is Partly grounded), D-9 (a vote is a `feedback_events` row on the `interaction`)
- Journeys: J2 step 3, J3 step 2
- NFR touchpoints: NFR-6 (labelled controls, live region, keyboard reach), NFR-5 (a vote carries no conversation text beyond what already exists)

## Dependencies
- Requires: ASKCHAT-007 (the page and the transport), ASKCHAT-003 (the tool output and `data-deepfix-grounding` parts).
- Blocks: ASKCHAT-011 (the runbook checks these states on the deployment).

## Acceptance criteria

**The panel comes from the tool**

1. Given a message with `tool-search_documents` output parts, when the sources panel renders, then it lists one card per returned passage with `filename`, rank, score, and text, and shows a truncation note where `truncated` is true. It never parses the answer's prose to find passages.
2. Given a message with two tool calls, when the panel renders, then both results' passages appear, in call order then rank order, and each card is labelled with the query that produced it, so a follow-up turn's second search is not presented as a duplicate of its first.
3. Given a reloaded thread, when the panel renders, then it is rebuilt from the store and shows the same cards in the same order, with no second request per answer. A reloaded thread has no `tool-output-available` part to read, so `GET /ask/chat/threads/{thread_id}` returns each turn's stored `passages` (its `interaction_hits` snapshot) and the page falls back to it when the message carries no tool part.

**Votes still work**

4. Given a passage card, when the member votes up or down, then `feedbackService.submit` is called with `interactionId` for that turn, `target: "passage"`, and the rank the tool returned, and the returned vote is reflected on the card.
5. Given an answer, when the member votes on it, then the answer's vote is submitted with `target: "answer"` exactly as today.
6. Given a reload after voting, when the thread renders, then each card and the answer show the member's current vote, read from the stored interaction.
7. Given a vote that the API refuses, when it fails, then the card shows an inline retry and does not claim the vote was recorded.

**Citations**

8. Given an answer containing a validated `[n]` marker, when it renders, then the marker is a focusable control whose accessible name identifies the passage, and activating it selects and scrolls to that card in the panel.
9. Given a marker that resolved to nothing, when it renders, then it is plain text and is not a link. The server already dropped it from `citations` (ASKCHAT-003 AC 16). The substitution happens on the markdown *source*, inside the shared `ChatMarkdown`, via a private token a `p` override resolves into a control; walking the rendered tree does not work, because `ChatMarkdown` returns a `<div>` around an unrendered component and there are no string children to find.
10. Given the panel is open and a card is selected, when the member moves focus with the keyboard, then the selection follows and the selected card is visibly distinct.

**The badge (D-10)**

11. Given a turn with `grounding == "grounded"` and a non-empty `citations`, when the answer renders, then the badge is **Grounded** using the landed `GROUNDING_BADGE` copy and the note is `N passages used` from the landed `groundingNote`.
12. Given a turn with `grounding == "grounded"`, an empty `citations`, **and a search in that turn**, when the answer renders, then the badge is **Partly grounded** in the warning style and the note is still `N passages used`. It is a warning, not an error: it does not block the answer, the sources panel, or a vote.
12a. Given a turn with `grounding == "grounded"`, an empty `citations`, and **no search** — a rework of an earlier grounded answer, or a thanks — when the answer renders, then the badge is **Reused earlier evidence** in the primary style, not Partly grounded. The turn made no new claims and has no markers of its own, so warning about it is wrong. `searched` is the discriminator and is read per turn: a live turn from its own tool parts, a stored one from its `retrieval_query` and passages. The thread's newest passages are not the signal, because an earlier grounded answer leaves them on screen.
13. Given a turn with `grounding == "declined"` or `"no_relevant_sources"`, when the answer renders, then the badge is the landed **Not found in the documents** copy and the note is the landed `Answer declined` or `No matching passages` respectively.
13a. Given an answer that carries at least one valid citation, when its grounding is decided, then it is **Grounded** whatever its opening sentence or any refusal-shaped phrase inside it says. `grounding_for` takes `cited` and `_grounding` passes it from `valid_citations`: a live answer that opened by framing itself, cited five passages, and was graded `declined` because the phrase list matched a substring is the defect this rule exists to prevent (D-12).
14. Given the badge implementation, when it is read, then it extends `web/src/features/chat/grounding-copy.ts` in one place rather than adding a second badge component or a second copy map. `GROUNDING_BADGE` is a `Record<Grounding, …>`, so the fourth *rendered* state cannot be a fourth key without widening the enum D-3 forbids; it is `badgeState(grounding, citations)`, beside `groundingNote`, returning one of four states over three stored values. `GroundingBadge` takes `citations` and renders that state.
15. Given a `no_relevant_sources` turn, when it renders, then there are no passage cards, the badge and note are present, and there is no Regenerate control. The rendered states are distinguishable without reading the answer's prose (FR-034).
15a. Given a `not_searched` turn — the answer came from no search and no earlier evidence — when it renders, then the badge reads **Not searched** with the note "Answered without searching", it is neither Grounded nor Partly grounded nor "Not found in the documents", and there are no passage cards. A turn that did not search **but reuses evidence already in the thread** is `grounded` instead, because that content did come from the corpus — and the two are told apart by the order of the checks: whether this turn searched first, then what it found. `no_relevant_sources` requires a search that ran and found nothing, and is never applied to a turn that did not search.

**Accessibility**

16. Given a turn streaming, when it renders, then the streaming region is `aria-live="polite"`, Stop and Regenerate are named buttons, the panel is reachable by keyboard, and focus returns to the composer when the turn ends. The panel is reachable at every width: it sits beside the column and **stacks below `lg`**, because hiding it on a narrow viewport makes the evidence unreachable on a phone.
17. Given the badge, when it is read by a screen reader, then its state is text, not colour alone.

**The required test**

18. Given `web/src/test/ask-chat-sources.test.tsx`, when it runs, then: a frame set with two passages and a `[1]` citation renders two cards, the Grounded badge, and a citation control that selects the first card; the same answer with no marker renders Partly grounded and still shows both cards; an empty tool result renders the "Not found in the documents" badge with no cards and no regenerate; a vote on a passage calls the feedback endpoint with the rank the tool returned; and the live region is asserted on the streaming element. The component is real and the transport boundary is stubbed with frame fixtures, as `web/src/test/admin-chat-session.test.tsx` already does.

## Out of scope
- Re-ranking, re-ordering, or re-scoring passages in the browser. The scores shown are the tool's.
- Voting on a passage from a turn whose interaction could not be stored. That turn shows its error instead.
- Highlighting the cited sentence inside the passage text.
- A second badge component, a second copy map, or a new grounding status. ASK-001 owns the enum and D-3 forbids widening it.
- Changing the admin Hermes chat's rendering. It shares `ChatMarkdown`, not this panel.

## Notes
- The card's text must come from the tool output's `text`, and the card's vote must use the same rank. If the panel showed a different order from the stored hits, passage votes would resolve to the wrong passage — silently, because the rank would still exist.
- The new "Partly grounded" state belongs in `grounding-copy.ts` beside the existing three keys so there is one place a reader looks for what a badge means. It is a fourth *rendered* state over three *stored* values, which is why it needs a citation count rather than a new enum value.
- `GROUNDING_BADGE` is a `Record<Grounding, …>`, so the partly-grounded label cannot be a fourth key without changing the enum. Model it as a function of `(grounding, citationCount)` next to `groundingNote`, which already takes the source count and is therefore the natural shape.
- A vote on a stopped or failed turn is allowed when the turn stored its interaction; the sources panel for a stopped turn survives Stop by design (ASKCHAT-004 AC 2), so the two must agree.

## Definition of done
- [ ] Code complete
- [ ] `web/src/test/ask-chat-sources.test.tsx` green
- [ ] `web/src/test/pages.test.tsx` and CHAT-016's shared-component expectations still green
- [ ] `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build` pass
- [ ] Committed on its own before ASKCHAT-009 starts

# Story validation: ASKCHAT-008 — Show the evidence, follow its citations, and say how grounded it is

- Story file: `tasks/stories-ask-chat/ASKCHAT-008-sources-citations-grounding.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. Every AC names a rendered element, a service call with its arguments, or a badge label.

### Should-fix

Three were found and fixed; the third is the one a reviewer would otherwise catch.

1. **AC 14 and the Notes described the badge as a `Record` that gains an entry.** `GROUNDING_BADGE` is `Record<Grounding, {label, className}>` in `web/src/features/chat/grounding-copy.ts`, so a fourth key is impossible without adding a fourth enum value — which D-3 forbids. "Partly grounded" is a fourth *rendered* state over three *stored* values, so it must be a function of `(grounding, citationCount)` modelled beside `groundingNote`, which already takes the source count. AC 14 and the Notes now say that.

2. **AC 13 was written against labels the landed code does not use.** ASK-001's copy is "Not found in the documents" for both `declined` and `no_relevant_sources`, separated by the note (`Answer declined` / `No matching passages`). The story now names the landed strings rather than inventing "Declined" and "No relevant sources" labels, so an implementer does not rewrite copy that already ships and is already tested.

3. **AC 2 introduced a per-query grouping for multiple tool calls** that the panel's data does not obviously support without deciding an order. It now states the order (call order, then rank order) and that each card is labelled with the query that produced it — which matters, because a follow-up turn's second search would otherwise look like a duplicate of the first.

### Nice-to-have

- The story does not specify how a citation control's accessible name identifies its passage. It says it must, which is the testable part; the exact wording is a copy decision.

## Suggested edits (applied)

- AC 14 and the Notes rewritten around the `(grounding, citationCount)` shape.
- AC 13 pinned to the landed strings.
- AC 2 given an order and a query label.

## PRD alignment

- FRs: FR-007 (the panel from tool results), FR-030 (the panel, the citation controls, the a11y), FR-034 (the badge half of the no-sources state).
- Decisions: D-3 (grounding plus validated citations), D-10 (a grounded but uncited answer is Partly grounded — the owner's answer of 2026-11-15), D-9 (a vote is a `feedback_events` row on the interaction, which is why the rank must be the tool's).
- Gaps: none. The PRD's D-10 table and this story now agree on all four rendered states, including the two notes; the PRD was reconciled in the same pass that produced this story.

## NFR touchpoints

- **NFR-6** — AC 8, AC 10, AC 16, AC 17: the citation control is focusable and named, the panel is keyboard navigable with a visible selection, the streaming region is `aria-live="polite"`, and the badge state is text rather than colour alone.
- **NFR-5** — AC 4, AC 5: a vote carries only what `feedback_events` already stores for a passage vote, so the panel adds no new data category.
- **Correctness the story depends on** — AC 4's rank must be the tool's rank, which is the same rank stored in `interaction_hits` (ASKCHAT-003 AC 14). A different order would send a vote to the wrong passage while still resolving, which is the failure mode with no error message.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`838fdbf`)
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 3 said "rebuilt from the stored message parts" | AC 3 says the thread detail returns each turn's stored `passages` | A reloaded thread has no tool part. The stored `interaction_hits` snapshot is the only source, and the route had to grow a field for it |
| 2 | AC 14 said "one new entry and one new note" in `grounding-copy.ts` | AC 14 says `badgeState(grounding, citations)` returning one of four rendered states | `GROUNDING_BADGE` is `Record<Grounding, …>`; a fourth key would widen the enum D-3 forbids. The story's own note prescribed the function shape, and the AC contradicted it |
| 3 | AC 16 did not say where the panel sits | AC 16 says it stacks below `lg` | As laid out in ASKCHAT-007 the panel was `hidden` below `lg`, so a phone could not reach its evidence at all |
| 4 | AC 9 did not say how a marker becomes a control | AC 9 says the source is rewritten with a private token that `ChatMarkdown`'s `p` override resolves | A walk over the rendered tree finds no string children, because the renderer wraps an unrendered component |

### Blockers

None.

### Should-fix (applied)

1. **A marker in a heading or a list item renders as its characters, not a
   control.** Only paragraphs are rewritten. A citation belongs in a sentence, and
   widening it would mean overriding every block type for a case that does not
   occur; the limit is stated in the code.

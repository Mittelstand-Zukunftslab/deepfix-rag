# Story validation: ASKCHAT-009 — Bring the browser's existing conversations across, once

- Story file: `tasks/stories-ask-chat/ASKCHAT-009-import-local-sessions.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. The outcome is single — a member's existing conversations arrive once and the browser stops holding them — and every AC names a route response, a stored row, a key's removal, or a request count.

### Should-fix

1. **AC 6's "larger than a sane bound" was not testable.** The route accepts a client-supplied list, so the bound is a real input validation rather than a nicety. Rewritten to a 400 with a sentence naming the limit, which is what `AGENTS.md` asks of a validation failure; the number itself is the implementer's to pick and record.
2. **AC 8 needed a stated reason.** "The keys are removed anyway" looks like carelessness until you read why: leaving a member on a store the server cannot see is worse than telling them once, and a retry loop would keep the same conversations alive in two systems with different contents. The reason is now in the AC and in the Notes.

### Nice-to-have

- The story does not fix the response shape (`{created, skipped}` and so on). It requires the counts in AC 1 and AC 10, which is the testable part; the field names are the implementer's.

## Suggested edits (applied)

- AC 6 pinned to a 400.
- AC 8 given its reason.

## PRD alignment

- FRs: FR-033 (import once, link by interaction id, skip what cannot be linked, remove the keys).
- Decisions: D-5 (server-side threads and the one-time best-effort import, with key deletion as the stated fallback if the story is cut), D-9 (the import never invents an `interactions` row, because a fabricated one would be a question the optimizer later scores with no passages).
- Gaps: none. This is the only Should story in the set, and the PRD already states the fallback.

## NFR touchpoints

- **NFR-4** — AC 2, AC 5: this is the one place the server reads a client-supplied list of interaction ids, so the ownership check is a security boundary rather than a filter. A member who could attach another member's interaction to their own thread would read its passages and votes.
- **NFR-5** — the import moves answer text and an interaction reference; it copies no passage text, because the interaction already holds the hits and the page reads them through the existing routes.
- **NFR-2** — AC 4: idempotence per session id and per interaction id, so a member with two browsers does not end up with two copies of the same conversation.

## Note for `check-story-size`, not a finding here

Eleven acceptance criteria, one route and one client effect — comfortably within a single session. Nothing to split.

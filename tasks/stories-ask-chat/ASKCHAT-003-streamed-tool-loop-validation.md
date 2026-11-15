# Story validation: ASKCHAT-003 — Stream the answer as the AI SDK UI message stream

- Story file: `tasks/stories-ask-chat/ASKCHAT-003-streamed-tool-loop.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. This is the largest story in the set, but it has one outcome — a question is answered by a loop that searches and streams — and every AC names a part type, a stored row, a status code, or a function call.

### Should-fix

1. **AC 10 and AC 14 overlap on what the model sees versus what is stored**, and a reader could conflate them. They are now distinguished: AC 10 is the upstream message array (system prompt, stored history, new user message); AC 14 is the `interactions` row and its hits. Both are required and neither substitutes for the other.

### Nice-to-have

- The prompt's exact wording is not specified. That is deliberate: it is a thing to iterate on, and pinning it in a story would make every prompt edit a story amendment. AC 11 states the four properties the prompt must have, which is what a test can check.

### Note for `check-story-size`, not a finding here

This story is large: twenty acceptance criteria spanning the route, the loop, the prompt, the storage, and the contract. `validate-story` does not judge size — `check-story-size` does — so this is recorded for that step rather than as a defect. It should stay whole: splitting it would produce a route that streams without retrieving (unshippable) or a loop that retrieves without streaming (a second protocol to throw away). The mitigation is that its required test is one file, run at four points.

## Actions for the implementer (not defects)

1. **AC 19's fake endpoint must be a real server.** `LLM_BASE_URL` pointed at a loopback `uvicorn` that emits `chat.completion.chunk` frames with `tool_calls`, not a monkeypatched `litellm`. `AGENTS.md` forbids mocks, and the whole point of this test is that the HTTP path works.
2. **AC 2's schema fixture is a committed artifact.** The AI SDK's chunk schema must be checked into the test directory so the assertion does not depend on a network fetch, and so a future SDK bump that changes it fails visibly.
3. **AC 16 is the only place the citation rules are checked end to end.** Do not let it become a pure-function test of the marker parser — the value is that the markers came off the wire and resolved against the passages the tool returned.

## PRD alignment

- FRs: FR-001, FR-002, FR-003, FR-005, FR-006, FR-008, FR-009, FR-011, FR-012, FR-013, FR-019 (the row half), FR-025, FR-031, FR-034 (the server half). This is the widest FR span in the set, which is why it is the pivot story.
- Decisions: D-1 (the loop is in the API), D-2 (one tool, always runs, bounded calls), D-3 (grounding plus validated citations), D-5 (`kind='ask'`), D-6 (served equals scored), D-7 (`LLM_MODEL`, no setting — AC 5 is the guard).
- Gaps: none. FR-007 (the panel from tool results) belongs to ASKCHAT-008 because it is a rendering requirement; the data it needs is this story's `tool-output-available` part.

## NFR touchpoints

- **NFR-4** — AC 1, AC 13: no model id and no credential in any frame; a passage containing an instruction is not followed. The builder pattern in `chat_stream.py` is why no upstream object is forwarded and why no `model` field can leak.
- **NFR-5** — AC 14: one `interactions` row, no conversation dump. The trace is ASKCHAT-010's, and it extends this row rather than adding a second.
- **NFR-7** — the story adds no runtime and no protocol; it speaks the one the admin chat already speaks.
- **NFR-8** — the Notes require one structured line per turn start and terminal state, with no passage text.
- **Accessibility** — not implied: this story ships no UI. ASKCHAT-007 and -008 carry it.

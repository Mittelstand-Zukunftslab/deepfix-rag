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

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`09cd26f`) and after the deployed defect
  was fixed (`3b5d24f`)
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 2 listed one linear part sequence | AC 2 states the per-step grammar, with two `start-step`/`finish-step` pairs for a two-step turn | `start-step`/`finish-step` bracket *model steps*. The drafted list described a turn with one step and put the tool parts outside any step |
| 2 | AC 5 said `drop_params` came from `litellm_additional_kwargs()` per call | AC 5 says it is set once at import and the loop does not re-set it | That helper assigns the process-wide `litellm.drop_params`; calling it inside a request is a global side effect racing every other request |
| 3 | AC 10 read history from `ask_chat_turns` | AC 10 reads turns joined to `interactions`, because the text lives there | The turn table has no question and no answer (FR-019) |
| 4 | AC 14 implied the interaction row was written at the end | AC 14 says it is opened before the first byte and filled in afterwards | A cancelled or failed turn must be readable, and a regenerate must rewrite the same row |
| 5 | Nothing said a provider could refuse the forced step | AC 6 disables thinking on it and AC 6a recovers from a refusal | The deployed stack failed **every** turn this way; the drafted story assumed the hint was honoured |
| 6 | The test plan named a "local fake endpoint" without a recovery case | AC 19 names both recoveries and the exact wire body assertion | The failure was invisible to every local test because none refused the forced shape |

### Blockers

None outstanding. The defect itself (every turn failing at step 0) was a
**blocker in the shipped code**, found on the deployment and fixed in `3b5d24f`
with an integration test that reproduces the exact 400 over a socket.

### Should-fix (applied)

1. **The trace could not carry child spans.** D-4's "one child span per tool
   call" is impossible with `trace_request`, which opens and closes its span
   before the loop runs. The story now points at `tracing.turn_trace`, a context
   manager held open for the turn.
2. **A failed turn's trace was reported as healthy.** `turn_trace` initially
   swallowed an exception raised by its own body, so a failed turn left a
   `TRACESTATUS.OK` trace. It now re-raises, and FR-020 has the route mark the
   span `ERROR`.

### Nice-to-have

1. A provider that refuses the *unforced* retry too is recovered by the
   server-side search, which means the model's first real step is answering with
   a tool result already in the conversation. That is the correct behaviour and is
   tested, but it is worth watching in the trace: a high rate of it would mean the
   served provider needs replacing via `LLM_MODEL`, not another workaround.

## Post-implementation validation, second pass (2026-11-16)

- Validated: after the `tool_choice: auto` change and the fragmented-tool-call fix
- Verdict: **Pass with actions, all applied**

Two live defects forced changes to this story after it was first marked
implemented. Both were invisible to the suite as written, which is the finding
worth recording.

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 6 forced a tool call on the first step, with a provider-specific reasoning flag | AC 6 says every step is `auto` and only the last is `none`, with no reasoning flag | Forcing was the wrong rule: it broke on reasoning providers and made every "thanks" search. D-4b moves the decision into the system prompt |
| 2 | `_delta_tool_calls` mapped each delta's `tool_calls` to a whole call | AC 2a requires accumulating fragments by `index` across the step, then parsing once | The deployed provider sends one call as ~10 deltas at `index: 0`; per-delta mapping produced `search_documents({})` and then an unknown-tool error |
| 3 | AC 19's test list assumed a model that ignores `tool_choice` should trigger a server search | AC 19(b) asserts a non-searching turn is `not_searched` and is never Grounded | The old criterion described behaviour the product no longer wants: a legitimate small-talk turn must not be forced into a search |
| 4 | AC 5's `drop_params` handling was per call | Unchanged, and still correct: `llm.py` sets it once at import | Verified again while rewriting the call |

### Blockers

Both defects were blockers in shipped code and are fixed. The second is the more
instructive: **every fixture in this file sent a complete tool call in one chunk**,
so a suite that exercised the loop end to end could not distinguish "assembles a
call" from "treats each delta as a call". AC 19(e) is that missing fixture.

### Should-fix (applied)

1. The assembly uses `litellm.stream_chunk_builder`, the library's own merge for
   exactly this shape, rather than the hand-rolled accumulator first written for
   it. The repository's rule is to reach for the established dependency first, and
   this is a case where the dependency is doing real work: it takes the id and name
   from the piece that carries them and concatenates the argument fragments.

### Nice-to-have

1. A provider could in principle split one call across two different `index`
   values, which would be a protocol violation. `stream_chunk_builder` would build
   two calls and the loop would run two searches, which is harmless but wasteful.
   Not worth defending against.

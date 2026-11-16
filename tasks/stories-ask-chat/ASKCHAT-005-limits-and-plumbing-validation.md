# Story validation: ASKCHAT-005 — Bound one member and one deployment, and plumb every bound

- Story file: `tasks/stories-ask-chat/ASKCHAT-005-limits-and-plumbing.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. Every AC names a setting and its default, a file the name must appear in, a status code, or a stored row's absence.

### Should-fix

1. **AC 3 and AC 4 needed to be unambiguous about `example.env`.** AC 4 forbids a *variable* line matching `ASK_CHAT_.*MODEL`, and AC 3 permits one *comment* naming `qwen/qwen3.8-27b`. As first drafted a reader could think the two contradicted each other, or that the comment would fail its own guard. Both now say "comment" and "variable line" explicitly, and the guard is spelled as a variable-line match.
2. **AC 4 had no stated mechanism.** "There is no setting" is not testable without saying what is checked. Now: no `Settings` field matches `ASK_CHAT_.*MODEL`, and no variable line in `example.env` does either, both asserted by a test.

### Nice-to-have

- The limiter's `now` injection is in the Notes rather than an AC. It is an implementation consequence of "a test never sleeps out a window", which is `AGENTS.md` practice rather than a product requirement.

## Suggested edits (applied)

- AC 3 and AC 4 rewritten to distinguish a comment from a variable line.
- AC 4 given the assertion mechanism.

## PRD alignment

- FRs: FR-026 (rate and turn limits), FR-027 (per-turn bounds), FR-028 (no new footprint, and every setting plumbed).
- Decisions: D-8 (the limits and their defaults), D-7 (resolve `LLM_MODEL`; no `ASK_CHAT_MODEL` — the owner's answer of 2026-11-15, guarded here so a later story cannot reverse it).
- Gaps: none.

## NFR touchpoints

- **NFR-3** — AC 12: no new container, no `mem_limit` change, `backend-api` still uncapped.
- **NFR-4** — AC 7: a refusal writes no row, so it cannot ratchet itself into a longer ban.
- **NFR-8** — the sentence a refusal carries is what a member and an operator both read; AC 6 and AC 8 require it to name the wait or say the server is busy, which are different facts.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`289974a`)
- Verdict: **Pass**

No acceptance criterion needed correcting: the eight settings, their defaults, the
Compose and `example.env` lines, the model guard, and the two limits all shipped as
written. Two facts worth recording because they are evidence rather than opinion:

- The plumbing test was mutation-checked by deleting one `ASK_CHAT_*` line from
  `docker-compose.yml`; it failed naming the missing setting, which is the failure
  mode CHAT-001 introduced the test for.
- AC 11's history bound is now read from `ask_chat_turns` joined to `interactions`
  for the message text, which the story's own AC 1 did not have to say but AC 11
  does.

### Blockers

None.

## Post-implementation validation, second pass (2026-11-16)

- Validated: after a deployed turn answered nothing for 93 seconds
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 9 made the wall clock a per-chunk check | AC 9 requires each step to run inside `asyncio.timeout(remaining)`, and reaching it settles `failed` with the timeout sentence | `_check_deadline` only ran between chunks, so a provider that sent nothing could hold the turn open past the bound. A timeout stored as `cancelled` also told a member they had stopped something they had not |
| 2 | Nothing bounded the model's hidden reasoning | Two new settings: `ASK_CHAT_REASONING` (`off`) and `ASK_CHAT_MAX_OUTPUT_TOKENS` (2048) | The model reasoned in hidden mode by default and spent 93 s, and once the entire output budget, on thinking before any visible text (D-12) |

### Blockers

One: a turn could look hung for a minute and a half while a proxy prepared to drop it.

### Should-fix (applied)

1. The settings test enumerates every `ASK_CHAT_*` name, so a setting that reaches
   `Settings` but not Compose or `example.env` fails CI. It caught both new names in
   this change, which is the behaviour it exists for.

## Post-implementation validation, third pass (2026-11-16)

- Validated: after a live run showed one reasoning level cannot serve both steps
- Verdict: **Pass with actions, applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 1 named one reasoning setting | AC 1 names `ASK_CHAT_REASONING` (answer step, `off`) and `ASK_CHAT_TOOL_STEP_REASONING` (tool-capable step, `low`) | Thinking off made the model refuse before searching; thinking `low` on the answer step delayed the first word to ~9 s and once consumed the whole output budget. The two steps need opposite settings |

### Should-fix (applied)

1. The settings test enumerates every `ASK_CHAT_*` name and requires each on Settings,
   Compose and `example.env` with matching defaults. It caught the new name here, as
   it did the previous two.

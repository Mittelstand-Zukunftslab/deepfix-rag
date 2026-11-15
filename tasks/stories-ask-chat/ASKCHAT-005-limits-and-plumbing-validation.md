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

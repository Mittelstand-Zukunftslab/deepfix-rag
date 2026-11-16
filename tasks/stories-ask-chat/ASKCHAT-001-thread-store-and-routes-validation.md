# Story validation: ASKCHAT-001 — Keep conversations on the server, private to their owner

- Story file: `tasks/stories-ask-chat/ASKCHAT-001-thread-store-and-routes.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. Every acceptance criterion names a route and status code, a stored column and value, a rendered control, or a command's exit state. The single primary outcome — a conversation is a server object, private to one member — is not mixed with another.

### Should-fix

Three were found and fixed before this report was written.

1. **AC 2 was untestable as first drafted.** "exist exactly as D-5 defines them … the second run changes nothing" gives a tester no observation. Rewritten to assert the columns exist and that the second `init_ask_chat` adds and drops nothing — which is what idempotence means here.
2. **AC 12 said "several years old"**, which is a moving target. Pinned to a literal timestamp so a test can write that row and read it back.
3. **AC 15 listed routes without `operation_id`s.** The admin chat's convention is `list_admin_chat_threads` / `create_admin_chat_thread` / `get_…` / `delete_…` in `src/deepfix_rag/api/routes/admin_chat.py`, so the story now names the five `ask_chat` equivalents. Without them the generated client's method names are unnamed and two stories could pick differently.

### Nice-to-have

- AC 19 and AC 21 both require the regenerated client. AC 19 is the "the files match" check and AC 21 runs the tests that use the generated signatures; merging them would lose one of the two failure modes, so they stay separate.

## Suggested edits (applied)

- AC 2, AC 12, AC 15 rewritten as above.
- `README.md` records that the `## Dependencies` section is a `validate-story` check the skill's template omits.

## PRD alignment

- FRs: FR-010 (server-side threads, per member, delete semantics), FR-017 (no stuck `running` row), FR-019 (the schema half: one `interactions` row per turn with `kind='ask'`).
- Decisions: D-5 (the tables), D-9 (delete keeps interactions and votes), D-11 (no retention window), D-3 (the `Grounding` enum is stored, not widened).
- Gaps: none. FR-019 is split across ASKCHAT-001 (the columns and the unchanged `CHECK`) and ASKCHAT-003 (the row written per turn), which the PRD's Story to FR map states.

## NFR touchpoints

- **NFR-1** — AC 20 and the restart half of AC 21.
- **NFR-4** — AC 16, AC 17: 401 unsigned, 404 for another member's id rather than 403 so existence is not leaked.
- **NFR-5** — AC 10, AC 11: D-9 means a delete keeps the feedback record and the admin history keeps showing it, stated as a consequence rather than left implicit.
- **NFR-2** — the story adds no service; the tables are in the database the API already opens.
- **Accessibility** — not implied: the story ships no UI. ASKCHAT-007 carries the thread list's controls.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped on `feat/ask-chat-streaming`
  (`ff9434e` … `3b5d24f`)
- Verdict: **Pass with actions, all applied**

Re-run with `validate-story` against the code rather than the plan. Four things
the original acceptance criteria said did not match what the implementation had
to do, and each is corrected in the story file:

| # | Was | Now | Why it changed |
| :--- | :--- | :--- | :--- |
| 1 | `ask_chat_threads` had six columns and AC 2 asserted exactly those | It also has `legacy_session_id`, and `ask_chat_deleted_threads` is a third table | The import needs a per-session marker to be idempotent, and the idempotent delete needs a per-thread marker to tell "already deleted by this member" from "never theirs" |
| 2 | AC 18 required "thread, turns, and their grounding/citations" from the turn table | The turn table has no answer; the route joins `interactions` and returns each turn's stored `passages` | The answer belongs to the one `interactions` row per turn (FR-019), and a reload has no tool part to rebuild the sources panel from |
| 3 | AC 9's delete answered 204, and a second delete was unspecified | AC 9a states the idempotent 204 for the owner and the 404 for anybody else | A client that retries a dropped response was being told a successful request failed |
| 4 | AC 6's bound was measured on the raw title | AC 6 measures the trimmed title in the route *and* the store | A 120-character name with trailing whitespace was refused by the route and accepted by the store |

### Blockers

None.

### Should-fix (applied)

1. **AC 21's single end-to-end pass was doing too much to read.** Two further
cases were added as AC 22, one per behaviour: the idempotent delete with its
404 counter-cases, and the trim-then-measure rename. Each is a real integration
test against the app and a SQLite file, and each fails if its fix is reverted —
verified by reverting the marker row and the raw-length cap in turn.

### Nice-to-have

1. The delete marker table will grow with deletions and is never pruned. That is
   deliberate: it is three short columns per deleted thread, it is what makes the
   idempotence safe, and D-11's no-prune rule applies to it for the same reason —
   a prune would reintroduce the ambiguity between "deleted" and "never yours".
2. `GET /interactions/{id}` deliberately still answers 404 for another member's
   row and for a row deleted with its thread; only the *thread* delete is
   idempotent. If that asymmetry is ever a problem for a client, it is a product
   decision, not this story's.

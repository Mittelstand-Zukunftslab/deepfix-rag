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

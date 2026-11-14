# Story validation: CHAT-002 — Chat store and audit tables

- Story file: tasks/stories-admin-hermes-chat/CHAT-002-chat-store-and-audit-tables.md
- Validated: 2026-11-14 (first pass), 2026-11-14 (second pass, after D-6)
- Verdict: **Pass with actions**

## Second pass — after the retention decision (D-6)

### Blockers

1. **The story had no retention at all, and its out-of-scope list actively excluded it.** Since Fadel settled the
   14-day window (D-6, FR-030), a story that ships the audit table without a way to age it out would leave the FR
   reachable from no story — the same orphan problem the first pass found in NFR-4's route inventory.
   *Fixed:* new AC 14–18 add `prune_audit(db_path, retention_days, *, now=None) -> int`, the `0`/negative
   "keep forever" branch, idempotence, the "touches no other table and is not itself audited" invariant, and the
   timestamp-format requirement. AC 5's public-surface list gains the name; AC 19's test list gains the four
   retention cases; the out-of-scope list now excludes only the *wiring*, not retention itself.

2. **A literal `14` could have entered the module.** The window is a setting, but `prune_audit` is the obvious place
   to write a default. AC 14 now says `retention_days` has no default of its own and that the caller supplies
   `settings.ADMIN_CHAT_AUDIT_RETENTION_DAYS`, and the note forbids the module importing `Settings`.

### Should-fix

3. **The prune could have compared mismatched time formats.** `admin_chat_audit.at` is written by `record_audit`, but
   a test or a future caller could insert a row by hand. AC 18 pins the comparison to stored ISO-8601 UTC, matching
   `optimization._now()` and `feedback._now()`, so a row cannot become immortal by formatting.

4. **The ownership boundary with CHAT-006 was unstated.** A reader could not tell whether this story was supposed to
   start the prune. The dependencies line now says CHAT-006 wires it, and the out-of-scope section says why the two
   halves are separate commits: a function with no caller and a schedule with no function are separately testable.

### Nice-to-have
- AC 17 (no audit row is written for a prune) closes the "audit the audit" loop before someone adds it and creates a
  row that ages out on its own schedule.

### PRD alignment (second pass)
- FRs: FR-010, FR-016, FR-018, FR-021, FR-030 (the prune function).
- Decisions: D-4 (a delete never removes an audit row — unchanged and now stated as the settled decision rather than
  a recommendation), D-6 (the window is a setting, `0` disables it, the comparison is by `at`).
- Gaps: none. The retention half of D-6 that this story does not own (the hourly scheduler wake-up) is CHAT-006's,
  and the deployed check is CHAT-012's.

## First pass

### Blockers
None. Each AC names a schema object, a `PRAGMA`, an exception type, or a stored value a test can read back.

### Should-fix
None outstanding. Two were closed while drafting:

1. **The audit foreign-key rule was stated twice, in prose and again in the delete criteria.** It is now one AC
   (AC 4) checked with `PRAGMA foreign_key_list`, and AC 9/10 describe only the delete's effects.
2. **`redact` had no owner.** CHAT-003, CHAT-006, and CHAT-008 all need it, and none of them owns the module. AC 5
   makes `redact` a public member of `admin_chat` and AC 12 puts `HERMES_CHAT_API_SERVER_KEY` in `_SECRET_ENVS` —
   the one list — so the three later stories consume it instead of re-implementing it.

### Nice-to-have
- AC 13 defines `fail_interrupted_chat_turns` here even though CHAT-005 wires it into the lifespan. That is
  deliberate: the constant and the transition are storage, and CHAT-005 should not invent them.

## Suggested edits (concrete)
- None outstanding. Every finding in both passes is applied in the story file.

## PRD alignment (first pass)
- FRs: FR-010, FR-016, FR-018, FR-021. FR-016's step-tail half is CHAT-008's; this story supplies only the
  `admin_chat_thread_runs` table it reads.
- Decisions: D-4 in full — children-before-parent order, audit rows kept with no foreign key, idempotent second
  delete, one `thread_deleted` row.
- Gaps: none. (Retention, which this pass left out of scope as an open question, is the second pass's change.)

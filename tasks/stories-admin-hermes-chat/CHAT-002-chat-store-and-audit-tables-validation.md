# Story validation: CHAT-002 — Chat store and audit tables

- Story file: tasks/stories-admin-hermes-chat/CHAT-002-chat-store-and-audit-tables.md
- Validated: 2026-11-14
- Verdict: **Pass**

## Findings

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
- None outstanding.

## PRD alignment
- FRs: FR-010, FR-016, FR-018, FR-021. FR-016's step-tail half is CHAT-008's; this story supplies only the
  `admin_chat_thread_runs` table it reads.
- Decisions: D-4 in full — children-before-parent order, audit rows kept with no foreign key, idempotent second
  delete, one `thread_deleted` row.
- Open questions: retention is explicitly out of scope and points at the PRD's still-open question, so the story
  cannot be read as deciding it.
- Gaps: none.

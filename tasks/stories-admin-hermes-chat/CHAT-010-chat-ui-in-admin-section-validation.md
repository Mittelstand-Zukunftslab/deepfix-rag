# Story validation: CHAT-010 — The chat UI in the Admin section

- Story file: tasks/stories-admin-hermes-chat/CHAT-010-chat-ui-in-admin-section.md
- Validated: 2026-11-14 (first pass), 2026-11-14 (second pass, after D-6)
- Verdict: **Pass**

## Second pass — after the retention decision (D-6)

### Blockers
None. The only change was to copy, and it stayed a UI concern.

### Should-fix
One, closed while revising:

1. **The delete dialog promised a permanent record.** It said the record of a started run "is kept" (D-4), which was
   true when the table was unbounded and is now too strong: audit rows age out after
   `ADMIN_CHAT_AUDIT_RETENTION_DAYS` (D-6). A dialog that over-promises is a product defect even when the code is
   right.
   *Fixed:* AC 12 keeps the D-4 sentence about *what the delete removes and what it does not*, and adds that the copy
   must not promise an unbounded record — the trail is kept and ages out after the server's retention window. The
   dialog's accessible name and its two buttons are still asserted by AC 20, unchanged.

### PRD alignment (second pass)
- FRs: FR-022, FR-023, FR-025, FR-026, FR-027, FR-028 — unchanged.
- Decisions: D-4 (the dialog still names what survives the delete), D-6 (the copy no longer implies forever).
- Gaps: none. The retention window is not surfaced as a number in the UI, deliberately: it is an operator setting,
  and the dialog should not read a runtime value into user-facing copy.

## First pass

### Blockers

1. **The one backend change inside a UI story was unnamed.** AC 4 adds `chat_enabled` to the optimization status
   payload, which needs a `src/deepfix_rag/api/schemas.py` field, a `routes/optimization.py` change, a test in
   `tests/test_api_optimization.py`, and a regenerated `web/openapi.json`. Left implicit, an implementer could edit
   the schema and skip the pytest case, and the frontend would depend on an untested field.
   *Fixed:* AC 4 names all four artifacts and states that this is the story's only backend change.

2. **The generated-client breakage was described but not bounded.** AC 6 said the `status()` fixture gains
   `chat_enabled` "and wherever else an `OptimizationStatus` literal is built". A validator cannot tell whether that
   is one site or twenty.
   *Fixed:* AC 6 now requires the fixture plus a compile-clean `bun run typecheck` over the whole web tree, which is
   the only honest bound — the compiler finds the sites, and the AC makes the compiler the check.

### Should-fix

3. **The disabled state could be satisfied by a chat probe.** AC 5 now says the flag comes from the optimization
   query and the note forbids adding a probe, which is what FR-027's zero-request rule needs.
4. **The sidebar active-state fix had no test target.** AC 3 names the two URLs and the exact single-active
   expectation at each.

### Nice-to-have
- AC 8 records rather than asserts that `@assistant-ui/react` 0.15.28 supports `onNew`/`onReload`/`onCancel`,
  because `web/node_modules` is absent in this checkout and the PRD lists the same gap as an unverified claim.
- The verification note checklist mirrors the PRD's CHAT-010 verification sentence exactly.

## Suggested edits (concrete)
- None outstanding. Every finding in both passes is applied in the story file.

## PRD alignment (first pass)
- FRs: FR-022, FR-023, FR-025, FR-026, FR-027, FR-028 — all six present as ACs.
- Decisions: D-2 (disabled page, zero requests, sidebar entry stays visible), D-3 (misconfigured message distinct from
  disabled), D-4 (delete dialog copy names what survives).
- NFRs: NFR-6 as AC 19, NFR-7 as AC 8 and the out-of-scope list.
- Gaps: none. An audit **view** is out of scope, as in CHAT-006.

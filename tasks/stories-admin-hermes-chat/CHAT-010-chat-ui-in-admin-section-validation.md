# Story validation: CHAT-010 — The chat UI in the Admin section

- Story file: tasks/stories-admin-hermes-chat/CHAT-010-chat-ui-in-admin-section.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

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
- None outstanding.

## PRD alignment
- FRs: FR-022, FR-023, FR-025, FR-026, FR-027, FR-028 — all six present as ACs.
- Decisions: D-2 (disabled page, zero requests, sidebar entry stays visible), D-3 (misconfigured message distinct from
  disabled), D-4 (delete dialog copy names what survives).
- NFRs: NFR-6 as AC 19, NFR-7 as AC 8 and the out-of-scope list.
- Gaps: none. An audit **view** is out of scope, as in CHAT-006.

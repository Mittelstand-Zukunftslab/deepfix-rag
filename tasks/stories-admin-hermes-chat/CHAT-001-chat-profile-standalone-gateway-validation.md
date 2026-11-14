# Story validation: CHAT-001 — Start the chat profile as a gated standalone gateway

- Story file: tasks/stories-admin-hermes-chat/CHAT-001-chat-profile-standalone-gateway.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **AC 15 asserted behaviour of routes that do not exist at this commit.** It required the 503 set across every
   `/admin/chat/*` route plus the audit and delete exemptions, but CHAT-001 is the first story and registers no
   routes. An implementer could not make that AC true without doing CHAT-003's work in CHAT-001, or would have had
   to fake it.
   *Fixed:* AC 15 now specifies only what this commit can ship — a router module with `DISABLED_CODE`,
   `DISABLED_MESSAGE`, `GATE_EXEMPT_PATHS`, and a unit-tested `require_chat_enabled(settings)`. The end-to-end 503
   set is attributed to CHAT-003 (which adds the gate dependency and an "every route is gated or exempt" test),
   CHAT-005 and CHAT-006 (which add the two exempt routes), and CHAT-012 (which checks all nine on the deployment).

### Should-fix

2. **AC 12 attributed the model fallback to the wrong function.** `chat_profile_config` takes an explicit `model`
   argument; the fallback is not its job, so "when `chat_profile_config` is called for each case" described a test
   that could not fail for the right reason.
   *Fixed:* the AC now places the resolution in `apply_sandbox` (one small resolver, three cases) and asserts the
   value written into `profiles/chat/config.yaml`.

3. **The stale-prose greps had no test.** The four prose sites were named but nothing enforced them, so the next
   reader could re-introduce "not started".
   *Fixed:* AC 14 now requires a test that greps the four sources and fails on either phrase.

4. **AC 17 and 18 need a container runtime.** This VM has none, which would have read as an unfinished story.
   *Fixed:* the story states the limitation explicitly, records the checks as not-run here, and defers them to
   CHAT-012 — the same convention `tasks/prd-hermes-optimizer.md` US-007 used.

### Nice-to-have

5. The `example.env` note now says line 104 already carries `HERMES_CHAT_API_SERVER_KEY` and must not be duplicated.
6. The Compose-default rule (`${VAR:-default}` for typed fields, `${VAR:-}` for `str` only) is stated once and tied
   to the AC that proves it, rather than left as folklore.

## Suggested edits (concrete)
- None outstanding. The five findings above are applied in the story file.

## PRD alignment
- FRs: FR-001, FR-002, FR-020, FR-029 — all present, all traceable to the PRD's story-to-FR map.
- Decisions: D-1 (default off, standalone, no memory when off, boot refuses without `gateway.standalone`), D-3
  (fallback and the commented-out `example.env` line).
- Gaps: none. The PRD's "Amendments required in `tasks/prd-hermes-optimizer.md`" items 1 and 3 are deliberately not
  applied here; item 3's edit belongs to CHAT-011, which reverses US-007's plugin exclusion, and item 1's pointer
  travels with it.

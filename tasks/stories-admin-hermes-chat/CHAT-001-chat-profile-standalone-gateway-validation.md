# Story validation: CHAT-001 — Start the chat profile as a gated standalone gateway

- Story file: tasks/stories-admin-hermes-chat/CHAT-001-chat-profile-standalone-gateway.md
- Validated: 2026-11-14 (first pass), 2026-11-14 (second pass, after D-6)
- Verdict: **Pass with actions**

## Second pass — after the retention decision (D-6)

### Blockers

1. **The new retention setting would have been invisible to the API container.** This story owns the plumbing that
   makes every later setting reachable, and `docker-compose.yml` gives `backend-api` an explicit allowlist with no
   `env_file`. A setting added to `Settings` but not to that allowlist is silently empty at runtime — the exact
   failure the PRD's second validation pass closed for the other chat settings.
   *Fixed:* AC 1 now requires eleven `ADMIN_CHAT_*` settings including `ADMIN_CHAT_AUDIT_RETENTION_DAYS=14`; AC 2
   includes it in the `backend-api` environment list; AC 5 documents it in `example.env` with the `0` case; AC 6's
   plumbing test covers it automatically because it walks every `ADMIN_CHAT_*` name.

### Should-fix

2. **`example.env` had no place to explain `0`.** AC 5 now requires the comment to say that `0` keeps audit rows
   forever, because an operator who wants the unbounded behaviour has no other way to learn it.

### Nice-to-have
- AC 2's non-empty-Compose-default rule already covered the new `int` field; no new rule was needed, which is the
  point of having stated it once.

### PRD alignment (second pass)
- FRs: FR-001, FR-002, FR-020, FR-029, and the plumbing that makes FR-030 reachable.
- Decisions: D-1 and D-3 unchanged; D-6 added (the retention setting is plumbed like the rest, with a non-empty
  Compose default).
- Gaps: none. The prune itself is CHAT-002's function and CHAT-006's wiring; this story only makes the setting exist
  where the API can read it.

## First pass

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
- None outstanding. Every finding in both passes is applied in the story file.

## PRD alignment (first pass)
- FRs: FR-001, FR-002, FR-020, FR-029 — all present, all traceable to the PRD's story-to-FR map.
- Decisions: D-1 (default off, standalone, no memory when off, boot refuses without `gateway.standalone`), D-3
  (fallback and the commented-out `example.env` line).
- Gaps: none. The PRD's "Amendments required in `tasks/prd-hermes-optimizer.md`" items 1 and 3 are deliberately not
  applied here; item 3's edit belongs to CHAT-011, which reverses US-007's plugin exclusion, and item 1's pointer
  travels with it.

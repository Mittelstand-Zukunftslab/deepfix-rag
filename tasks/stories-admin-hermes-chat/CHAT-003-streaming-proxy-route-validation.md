# Story validation: CHAT-003 — The streaming proxy route

- Story file: tasks/stories-admin-hermes-chat/CHAT-003-streaming-proxy-route.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **AC 8 and AC 21 required nine routes and two exempt routes to answer, none of which exist at this commit.** The
   PRD's CHAT-003 wording assumes the whole router is in place, which it is not: threads are CHAT-005, actions are
   CHAT-007, audit is CHAT-006.
   *Fixed:* AC 8 now asserts the disabled 503 on `POST /admin/chat/completions` (the only Hermes-facing route at
   this commit) plus an inventory test — "every route currently under `/admin/chat` is either gated or in
   `GATE_EXEMPT_PATHS`" — which is what keeps the property true as the router grows without re-opening this story.
   AC 21's test list was trimmed to match. The complete nine-route check is CHAT-012's AC 3, and each exempt route's
   own behaviour is asserted by the story that adds it.

2. **AC 9 claimed the audit and delete routes work with the key unset, but neither route existed.** It now states the
   structural fact instead: the configuration check is a *separate* dependency applied only to Hermes-facing routes,
   so the exempt routes cannot inherit it. That is testable here and is what CHAT-005 and CHAT-006 rely on.

### Should-fix

3. **AC 11 (the `api-result.ts` change) is web code inside an API story.** Kept, because AC 10's `ErrorResponse`
   union changes the generated TypeScript error type and leaving the reader broken for one commit would make the
   web suite red on this commit. It is named explicitly so the implementer does not treat it as a surprise.

4. **`system_prompt` did not exist yet but the AC assumed it.** The out-of-scope section now says this story injects
   a policy-text-only prompt through `admin_chat.system_prompt`, and CHAT-004 adds the digest to it. Without that,
   two stories would both claim to author the same function.

### Nice-to-have
5. The note about reusing `hermes_client`'s SSE parser rather than writing a second one, since that parser already
   handles `: keepalive` and redaction.
6. The four constants (two codes, two sentences) are pinned to one module so CHAT-010's UI and CHAT-012's checks
   cannot drift.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-003, FR-004, FR-005, FR-007, FR-019, FR-021, FR-028. Every one is present as an AC.
- Decisions: D-2 (503 sentence and `code`), D-3 (fallback model in the upstream body, misconfigured 503 naming the
  variables), D-1 (the gate, with the exempt paths as constants).
- Gap closed: the PRD's check-order paragraph is an AC (AC 13), including the `current_user` 503 with no `code` so
  the three 503 bodies are distinguishable (AC 12).
- Gap remaining, by design: the PRD's "nine routes return 503" cannot be a single-story AC; it is asserted
  incrementally here and completely in CHAT-012.

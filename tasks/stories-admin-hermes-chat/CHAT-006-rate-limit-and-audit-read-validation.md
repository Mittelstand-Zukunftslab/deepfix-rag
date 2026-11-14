# Story validation: CHAT-006 — Rate limit, audit read, and the retention prune wiring

- Story file: tasks/stories-admin-hermes-chat/CHAT-006-rate-limit-and-audit-read.md
- Validated: 2026-11-14 (first pass), 2026-11-14 (second pass, after D-6)
- Verdict: **Pass with actions**

## Second pass — after the retention decision (D-6)

### Blockers

1. **The audit read was complete but the table never shrank.** With D-6 settled, a story that documents "the audit
   table is the durable record" and ships no prune wiring would leave FR-030 half-implemented: CHAT-002's function
   with no caller. The PRD's retention decision names the scheduler tick, so this is the story that owns it.
   *Fixed:* AC 10–16 add the wiring with testable criteria: an optional `prune_audit` callable on
   `OptimizationScheduler`, invoked before the due-run check, at most once per hour, skipped when `None`.

2. **Wiring it to the tick as it exists today would have pruned about once a day.** `OptimizationScheduler._loop`
   waits until the next scheduled run — up to 24 hours with the default 02:00 cron — so "at most once per hour" would
   in practice have been "about once a day, at whatever hour the run happens". That is a retention policy by
   accident, and a reader could not tell whether it was intended.
   *Fixed:* AC 11 requires `_idle_delay(seconds_until_next_run) = min(that, 3600)` with three unit-tested cases
   (24-hour gap, 90-second gap, paused schedule), and D-6 in the PRD states the same change so the decision and the
   story agree. This is the finding I would most expect a reviewer to miss.

### Should-fix

3. **"Keeps working when chat is disabled" had no mechanism.** AC 12 now makes it structural: the scheduler knows
   nothing about `HERMES_CHAT_ENABLED`, and `app.py` injects a lambda that captures only the db path and the
   retention setting. A test constructs the scheduler with the flag off and asserts the row is gone.

4. **A raising prune could have killed the scheduler thread.** AC 14 requires the same catch-and-log shape as the
   existing `Scheduled optimization failed` path, with a test that injects a raising callable.

5. **The out-of-scope list still said retention was an open question.** Replaced, and the section now excludes only
   the prune implementation (CHAT-002) and the configurability of the hourly wake-up.

### Nice-to-have
- AC 15 restricts the log line to non-zero deletions, so an idle deployment writes nothing (NFR-8).
- AC 16 requires the setting to be read per invocation rather than captured at construction, so an operator's change
  takes effect on the next tick.
- The note pins the new scheduler tests to driving the tick body and `_idle_delay` directly, so no test sleeps an
  hour.

### PRD alignment (second pass)
- FRs: FR-017, FR-018, FR-030 (the wiring half).
- Decisions: D-4 (audit stays reachable with chat off — now also true of the prune), D-6 (hourly tick, setting read
  per run, `0` disables).
- NFRs: NFR-5 (the trail is bounded in time), NFR-8 (one line per prune that deleted something), NFR-2 (no new
  thread, no new process).
- Gaps: none.

## First pass

### Blockers
None. Every AC names a status code, a header, a row count, or a query shape.

### Should-fix
One, closed while drafting:

1. **"One `rate_limited` row per minute" could be read as a second limiter pass.** The PRD calls this out; the story
   now states in AC 5 that the count reads `admin_chat_turns` only, so an audit write cannot inflate it and a
   refusal cannot ratchet itself.

### Nice-to-have
- AC 10 forbids real sleeping and requires an injectable `now`. Without that the suite would take five minutes per
  run.
- The `thread_deleted` query is pinned to one `LEFT JOIN` (AC 7 and the note), so it cannot become an N+1.

## Suggested edits (concrete)
- None outstanding. Every finding in both passes is applied in the story file.

## PRD alignment (first pass)
- FRs: FR-017, FR-018. All present, including the `Retry-After` header and the two-level cap.
- Decisions: D-4 (audit rows survive a delete and are flagged at read time; the audit route stays available when
  chat is off), D-2 (`code` values are CHAT-003's, not re-declared here).
- Gaps: none. (Retention, which this pass recorded as out of scope, is the second pass's work.)
- Scope note: an audit **UI** is explicitly out of scope. The PRD names only the API route, and CHAT-010 builds the
  chat page. A validator or reader should not expect a screen for this.

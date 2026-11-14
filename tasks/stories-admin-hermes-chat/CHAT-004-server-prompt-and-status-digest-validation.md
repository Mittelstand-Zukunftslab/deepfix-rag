# Story validation: CHAT-004 — Server prompt and status digest

- Story file: tasks/stories-admin-hermes-chat/CHAT-004-server-prompt-and-status-digest.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **The overflow behaviour was asserted but not described.** AC 7 said the digest "raises" past the cap without
   saying what the admin sees, so two implementers would ship different behaviour (a 500, a silent truncation, or a
   dropped schedule section).
   *Fixed:* AC 7 now states the exact outcome — `ValueError` from `status_digest`, a 500 with a sentence naming
   `ADMIN_CHAT_DIGEST_MAX_CHARS`, and a `turn_failed` audit row. It is observable and a test can pin all three.

### Should-fix

2. **AC 13 referenced a fixture type that does not exist.** `_holdout_records` lives in `promotion.py`, not
   `feedback.py`; the AC now references `feedback.is_holdout` and a query id it marks as holdout, which is the
   function the PRD names.
3. **The gate thresholds could have become literals.** AC 3 now requires them to be read from settings.

### Nice-to-have
- AC 6's untrusted-data delimiters get a concrete test (AC 12) with an injected instruction in a run reason, which is
  the failure mode FR-006 names.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-006, and the server-owned-prompt half of FR-005.
- Decisions: none directly. The exact prompt sentence is a contract with CHAT-011 and is stated in both files.
- Gaps: none. Digest sources (`optimization_schedule.view()`, `champion_diff`, `recent_runs(limit=10)`) match the
  PRD's wording and the real function names in the tree.

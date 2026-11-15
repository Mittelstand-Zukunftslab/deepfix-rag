# Story validation: ASK-001 — Say what grounding an answer actually has

- Story file: `tasks/stories-ask-page/ASK-001-grounding-status.md`
- Validated: 2026-11-15
- Verdict: **Pass with actions**

## Findings

### Blockers

None. Every acceptance criterion names an observable response field, a rendered label, or a command's
exit state.

### Should-fix

1. **AC 9 is untestable as written.** "changes only where both surfaces legitimately want the change"
   gives a reviewer nothing to check. The admin chat shares only `web/src/components/chat/`, and this
   story touches neither module there — the badge is local to `chat-page.tsx`. Rewritten below as a
   file-level assertion.
2. **AC 8 left the replacement line unspecified.** "a factual line that a reader can verify" invites a
   vague label. The one fact the response already carries is the number of passages used, and the card
   shows the same number as its source count, so the line is pinned to that.
3. **AC 5 needed a stated derivation.** `grounding` is not stored, so a reader will ask what the read
   route returns for `kind: "retrieval"`. It is only defined for an ask; a retrieval interaction has no
   answer, so the field must still be present and must be `"no_relevant_sources"` — the honest reading
   of "there is no grounded answer here" — rather than null.

### Nice-to-have

- The story could name the module the shared phrase list moves to. It does now, in Notes.

## Suggested edits (concrete)

- AC 8 becomes: the footer shows `N passages used`, from `hits.length`, and no string in the card
  contains "Evaluated".
- AC 9 becomes: `git diff --stat -- web/src/components/chat/` is empty for this story, and
  `admin-chat-page.tsx` / `chat-session.tsx` are unmodified.
- AC 5 adds the retrieval case: `grounding` is present and is `"no_relevant_sources"`.

## PRD alignment

- FRs: FR-9 (snapshot and trace unchanged), FR-022 (the Ask screen's answer presentation), US-010.
- Gaps: none in the PRD. US-010 says "the answer first and the passages it used underneath"; this story
  makes the header honest about which of those actually happened.

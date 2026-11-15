# Story validation: CHAT-020 — Messages, markdown, and the action bar

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-messages-and-markdown.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. AC 7 as first drafted said "only the last assistant message offers regenerate"; that is ambiguous when a regeneration leaves two assistant messages in a row. Reworded to "when it is not the last answer".
2. Edit was in the original draft. The backend has no edit or branch route, so shipping an edit control would be a dead button. It moved to Out of scope and the ACs no longer mention it.
3. Feedback thumbs were in the draft action bar. There is no feedback adapter, so they would render as no-ops. Removed.
4. AC 9 (streaming indicator) had no observable form. It now requires the indicator to be visible while the run is running and gone once settled.

### Nice-to-have
- AC 10's scroll control already exists as `ThreadPrimitive.ScrollToBottom` in the installed thread element; the AC names its accessible name so the test can find it.

## Suggested edits (concrete)
- Keep `deepfix-tool` in the parts map, or the step row silently disappears; added to Notes.

## PRD alignment
- FRs: FR-022, FR-025.
- Gaps: none.

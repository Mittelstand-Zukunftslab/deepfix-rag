# Story validation: CHAT-017 — A LibreChat-style shell for the chat page

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-librechat-shell.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. AC 6 mixed two outcomes: the desktop collapse persistence and the mobile drawer. Both are one behaviour ("the sidebar is collapsible, with one remembered state and a drawer on small screens"), so they stay together, but the AC now names the breakpoint (`md`), which the implementation depends on.
2. AC 9's "truncated rather than wrapping" is observable only through a class name. Reworded to require `truncate` plus a `title` attribute, which is what a screen reader and a test can both see.
3. The story leans on `date-fns`; if the installed version has no relative-date helper that suits (it does: `isToday`, `isYesterday`, `differenceInCalendarDays`), a hand-written comparison is allowed but must be commented. Added to Notes.

### Nice-to-have
- A note that the sidebar is page-scoped state, not the app's global `useSidebarStore`, so a reader does not expect the two to move together. Added.

## Suggested edits (concrete)
- AC 6: bound the drawer to the `md` breakpoint and the stored preference to `localStorage`.
- AC 9: require `truncate` and a `title`.
- Notes: state that the page's sidebar state is its own store entry, distinct from the app shell's.

## PRD alignment
- FRs: FR-022, FR-023, FR-026, FR-027.
- Gaps: none. The PRD does not specify layout, so the LibreChat target is recorded as out-of-PRD styling with the owner's request as its source.

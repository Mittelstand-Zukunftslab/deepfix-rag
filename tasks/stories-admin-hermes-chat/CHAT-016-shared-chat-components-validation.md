# Story validation: CHAT-016 — One set of chat components for Ask and Hermes chat

- Story file: tasks/stories-admin-hermes-chat/CHAT-016-shared-chat-components.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. The first draft said "extract the chat UI" without a boundary, which would have licensed rewriting the Ask page. AC 2 and AC 3 now pin it: only the answer body and the composer bar change, the Ask tests are not touched, and a failure there is a regression rather than a test update.
2. AC 5 was added because the Ask page renders its answer inside a `prose` wrapper and the admin chat will not; without it, a later "cleanup" could quietly restyle every Ask answer.
3. AC 4's code-block behaviour had no owner. It is the shared component's, and the AC makes it observable on the Ask page rather than only on the new screen.
4. A shared send control was in the draft and was removed: the Ask page's request cannot be cancelled while the admin chat's button must become **Stop**. One component with a `streaming` flag would have given the Ask page a stop button it cannot honour, or the admin chat a spinner that cannot end a run. The boundary is the bar, not the button.

### Nice-to-have
- Keep `src/components/chat/` at two modules. The Ask page's history sidebar and the admin thread sidebar are different objects (localStorage sessions versus backend threads) and should not be forced together.

## Suggested edits (concrete)
- State the send-control split in the story's Out of scope, so the next reader does not try to unify them.

## PRD alignment
- FRs: FR-022, FR-026.
- Gaps: none. FR-026 is the constraint the story is written against: the Ask screen must not change beyond the markup it now imports.

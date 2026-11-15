# Story validation: CHAT-022 — Dark mode and the mobile drawer

- Story file: tasks/stories-admin-hermes-chat/CHAT-023-dark-mode-and-mobile.md
- Validated: 2026-11-15
- Verdict: Pass with actions

## Findings

### Blockers
None.

### Should-fix
1. The story adds a `.dark` token block to a shared stylesheet, which touches every page. AC 1 bounds it to the tokens `:root` already defines, and Out of scope says other pages are not reworked. That keeps the blast radius to "the existing `dark:` utilities start resolving".
2. AC 3's contrast requirement was vague ("readable"). It now names 4.5:1 for body text.
3. AC 4 and 5 split the drawer's layout from its keyboard contract; both are required, and the second is what makes it shippable under FR-025.
4. The appearance control was unspecified. AC 2 names its two accessible names, which is what a test and a screen-reader user both need.

### Nice-to-have
- `prefers-color-scheme` was considered as the default and rejected in Out of scope with the reason (it would change every other page's appearance without the admin asking).

## Suggested edits (concrete)
- Keep the appearance preference in `src/stores/`, beside the page's other client state.

## PRD alignment
- FRs: FR-022, FR-025, FR-026.
- Gaps: none; FR-026 is the constraint that the change stays additive.

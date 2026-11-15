# CHAT-022 — Dark mode and the mobile drawer

## Type
Feature

## Status
Draft

## Summary
The chat is readable and usable in both light and dark appearance, follows the same tokens as the rest of the web app, and collapses to a single column on a phone where the thread list becomes a drawer.

## PRD traceability
- Related: FR-022, FR-025, FR-026
- Out-of-PRD: dark mode and responsive behaviour were requested with the LibreChat redesign (owner request, 2026-11-15). The PRD's NFR-6 covers accessibility, which includes contrast in both appearances.

## Dependencies
- Requires: CHAT-017 … CHAT-021.
- Blocks: CHAT-023 (the screenshots include dark mode and mobile).

## Acceptance criteria

1. Given `web/src/styles.css`, when it is read, then it defines the `.dark` block the file's own header comment promises, with an oklch value for every token `:root` defines, and no component in `src/features/admin-chat/` introduces a colour that is not one of those tokens.
2. Given the appearance control in the chat header, when it is pressed, then the `dark` class is toggled on the document element, the choice is stored, and a reload keeps it; its accessible name is `Switch to dark appearance` or `Switch to light appearance` to match the state.
3. Given dark appearance, when the conversation renders, then assistant text, user bubbles, the composer, code blocks, action cards, the sidebar, and the thread list use the dark token values, and every text/background pair in the chat reaches at least 4.5:1 for body text.
4. Given the page below the `md` breakpoint, when it renders, then the conversation occupies the full width, the sidebar is closed, and the header's sidebar control opens it as a drawer over the conversation.
5. Given the drawer, when it is open, then focus moves inside it, Escape closes it and returns focus to the control, and the conversation behind it is not reachable by Tab.
6. Given a phone width, when a reply streams, then the composer stays docked and visible above the on-screen keyboard, and the message column keeps its `max-w-3xl` constraint with `px-4` gutters.
7. Given the composer and the sidebar at any width, when the page is zoomed to 200%, then no control overlaps another and the conversation still scrolls.
8. Given `web/src/test/admin-chat-page.test.tsx`, when it runs, then it asserts the appearance control's name and state, and that the mobile control has an accessible name.

## Out of scope
- A system-preference default: the control is explicit and remembered. Following `prefers-color-scheme` automatically would change appearance for every other page without the admin asking.
- Reworking other pages for dark mode beyond the token block they already inherit.

## Notes
- `styles.css` declares `@custom-variant dark (&:is(.dark *))` but has no `.dark` block, so every `dark:` utility in the app currently resolves to the light value. Adding the block is what makes the existing utilities active; it is a token addition, not a component change.
- The drawer uses the existing `Sheet` primitive from `src/components/ui/sheet.tsx`, which already traps focus and closes on Escape.

## Definition of done
- [ ] Code complete
- [ ] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-023 starts

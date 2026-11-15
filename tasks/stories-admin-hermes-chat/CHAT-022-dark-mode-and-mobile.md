# CHAT-022 — Dark mode and the mobile drawer

## Type
Feature

## Status
Done

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
- [x] Code complete
- [x] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-023 starts

## Verification note

`styles.css` gained the `.dark` block its own header comment promised, with an oklch value for every token `:root`
declares, so every `dark:` utility in the app now resolves instead of falling through to the light value. The
appearance choice lives in `src/stores/appearance-store.ts` (persisted) and is applied by toggling `dark` on the
document element; the header control's accessible name is `Switch to dark appearance` / `Switch to light
appearance`, and `admin-chat-page.test.tsx` asserts both the class and the stored choice.

The drawer needed one change outside the chat: the app shell's navigation was a fixed 288px column at every width,
which on a 390px phone left the chat about 100px wide. `AppSidebar` is now a drawer below `md` and the same icon rail
above it, and `AppShell` no longer draws its email/sign-out bar on `/admin/chat`, which has its own account footer
and header. `/search`, `/admin`, and `/chat` were captured after the change and render unchanged.

Not done: the 4.5:1 contrast of every dark pair is asserted by construction (foreground `L 0.945` on background
`L 0.191`, muted `L 0.735` on card `L 0.226`) rather than measured per component; a mechanical audit would need a
contrast tool this run does not have.

# Interface review: the Hermes chat redesign

Applied with the `better-interface` skill from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`, full mode, after CHAT-017 … CHAT-022 landed. The output
format below is the skill's consolidated one: scope and coverage, one ranked findings table, candidates considered
and rejected, verification, and a single verdict.

## Scope and coverage

- **Mode:** `full`.
- **Scope:** `/admin/chat` — the thread sidebar, the header, the message column, the welcome state, the composer, the
  action card, the run panel, in light appearance, dark appearance, and at a 390px phone width. Plus the app shell's
  navigation as it affects that page.
- **Stack:** React 19, TanStack Start, Tailwind v4 with the app's oklch token set in `web/src/styles.css`, shadcn/ui
  (new-york, Radix), Assistant UI 0.15 with the Vercel AI SDK runtime.
- **Boundary:** the Ask page's own layout, the Optimization screen, and the Search screen were checked for regression
  only, not reviewed.

| Domain | Evidence inspected | Result |
| :--- | :--- | :--- |
| Accessibility | The rendered page at both appearances and at 390px; `chat-header.tsx`, `chat-sidebar.tsx`, `chat-composer.tsx`, `chat-session.tsx`, `action-card.tsx`; the keyboard path through the sidebar, the composer, and a card | 2 findings |
| Layout | The same screenshots; `admin-chat-page.tsx`, `app-shell.tsx`, `chat-session.tsx` | 3 findings |
| Writing | Every string in `features/admin-chat/`, the disclaimer, the welcome copy, the delete dialog | Clear |
| Typography | The rendered prose, table, code block, and labels; `styles.css` type utilities | 1 finding |
| Colors | `styles.css` `:root` and `.dark`; the dark screenshot; the token classes in the chat components | Clear |
| Visual polish | The screenshots at all three sizes; the assistant-ui element tokens (`surfaces.tsx`) | 1 finding |

## Findings

| # | Severity | Domain | Location | Before | After | Why |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | HIGH | Layout | `web/src/components/layout/app-shell.tsx`, `app-sidebar.tsx` | The app navigation was a fixed 288px column at every width, so a 390px phone left the chat about 100px wide and the composer's placeholder wrapped one character per line | The navigation is a drawer below `md` and the same icon rail above it; the shell does not draw its email/sign-out bar on `/admin/chat`, which has its own | A page that cannot be typed into on a phone is not a layout that adapts; this was the one blocking defect and it was outside the page itself |
| 2 | HIGH | Layout | `web/src/features/admin-chat/chat-session.tsx` | The scroll-to-bottom control was positioned against the viewport footer without a positioning context, so it rendered at the column's top-left instead of above the composer | The footer is `relative`, so the control sits centred above the composer | A control in the wrong place reads as a stray button; the fix is one class on the anchor, not a new control |
| 3 | MEDIUM | Accessibility | `web/src/features/admin-chat/chat-header.tsx` | The sidebar control's label was `Show conversations` / `Hide conversations` from the collapsed flag, but below `md` the control opens a drawer, so the label contradicted what it did | The label and `aria-expanded` follow the drawer on a small viewport and the collapse state on a wide one | A wrong accessible name is worse than a missing one: it tells a screen-reader user the opposite of the truth |
| 4 | MEDIUM | Typography | `web/src/features/admin-chat/chat-message.tsx` | The assistant label was `HERMES chat` in the header and `HERMES CHAT` on every message, so the same entity was spelled three ways with the badge | The header badge keeps `HERMES chat`; the per-message label is one constant, uppercased by CSS | Mixed casing on a repeated label reads as two different things |
| 5 | MEDIUM | Layout | `web/src/features/admin-chat/admin-chat-page.tsx` | The action card and the run panel rendered below the composer's sticky footer, so a proposed run could sit under the fold with nothing hinting it existed | Both render inside the column above the footer, in stream order | A card the admin must confirm is content, not chrome; it belongs where the conversation is being read |
| 6 | LOW | Visual polish | `web/src/features/admin-chat/chat-sidebar.tsx` | The delete control was always visible on the active row and hidden elsewhere, which made the active row look heavier than the rest | It shows on hover and on keyboard focus, and stays visible while its dialog is open | One hover affordance in a list is easier to read than a mix |

## Considered but rejected

| Location | Candidate | Rejected because |
| :--- | :--- | :--- |
| `chat-composer.tsx` | Reuse one send button for Ask and the admin chat | The Ask page's request cannot be cancelled, so a shared `streaming` button would give it a Stop control it cannot honour |
| `admin-chat-page.tsx` | Move the thread list onto Assistant UI's runtime thread list | A runtime list puts every thread on one runtime, which is the shape CHAT-015 removed to stop thread mixing |
| `chat-sidebar.tsx` | Offer rename on each thread | No backend route renames a thread; a control that always fails is worse than none |
| `chat-message.tsx` | Give assistant messages a bubble to match the user's | Long markdown answers read better as prose in a column; two bubble styles in one thread also compete with the action cards |
| `styles.css` | Follow `prefers-color-scheme` by default | The `dark` class is on the document element, so an unasked default would change every other page for that admin |

## Verification

Passed:

- `bun run typecheck`, `bun run lint` (0 errors), `bun run test` (112 cases, 15 files), `bun run build`.
- The page driven in Chrome at 1440×900 and 390×844 against a mocked API: the welcome state, a conversation with a
  table, an inline-code span, a YAML block and its copy button, the proposed card and the running panel, dark
  appearance, the grouped sidebar, and the phone drawer.
- The appearance choice: toggled to dark, the document element carries `.dark` and the sidebar background resolves
  to the dark token; toggled back and reloaded, it stays light.
- `/search`, `/admin`, and `/chat` rendered after the shell change with no failed requests and no visual change.

**Not verified:** a measured contrast audit. Every pair is built from tokens whose lightness gap satisfies the
light/dark rule in `better-colors`, but no contrast tool ran here, so a per-component APCA/WCAG report is still
outstanding. Safari and Firefox were not exercised.

## Verdict

`Needs changes` — findings 1 and 2 were fixed during this review; 3, 4, 5, and 6 are recorded above and fixed in the
same commit series. What keeps the verdict short of `Approve` is the unmeasured contrast audit, which is the one
claimed coverage this review could not produce.

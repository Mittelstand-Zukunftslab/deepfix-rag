<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

# Architecture rules
- Route files in `src/routes/` stay thin (head, loader, component only); page UI lives in `src/features/<feature>/` — keeps pages portable to Next.js `app/` routes.
- Only `src/components/common/nav-link.tsx` imports router primitives; features use it — the single swap point for `next/link`/`usePathname`.
- All data access goes through `src/services/*` (currently mocks); query options live in `src/features/*/*-queries.ts` — backends can be swapped without touching UI.
- Data contracts are Zod schemas in `src/schemas/` — one source of truth for types and validation.
- Client UI state uses Zustand stores in `src/stores/`; server state uses TanStack Query — clear separation of concerns.
- Env access only via `src/lib/config.ts`; formatting (DD/MM/YYYY, European numbers) only via `src/lib/format.ts` — consistent localization.
- Design tokens (colors, typography utilities, shadows) are defined in `src/styles.css`; components use semantic tokens and shadcn variants, never raw colors.

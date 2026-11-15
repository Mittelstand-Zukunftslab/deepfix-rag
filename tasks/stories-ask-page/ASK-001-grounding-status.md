# ASK-001 — Say what grounding an answer actually has

## Type
Bug

## Status
Draft

## Summary
A member reading an answer on `/chat` can tell whether the corpus supported it. When the answer came
from the retrieved passages the card says **Grounded** and names its sources; when the model declined
because the corpus does not answer the question, the card says **Not found in the documents** and
offers no source count. The card also stops claiming an evaluation that never ran.

## PRD traceability
- Related: FR-9 (the interaction snapshot and its trace), FR-022 (the Ask screen), US-010
- Decisions: the grounding vocabulary and the shared deflection list are new; recorded here
- Out-of-PRD: none. The review at `642f720` is the trigger; the behaviour is inside US-010's screen.

## Dependencies
- Requires: nothing. `POST /ask`, `AskResponse`, and `chat-page.tsx` already exist.
- Blocks: ASK-002, which needs one of these states for "no passage passed the relevance floor".

## Acceptance criteria

1. Given `POST /ask`, when it answers with 200, then the body carries `grounding`, one of
   `"grounded"`, `"declined"`, `"no_relevant_sources"`, and the value is required (never absent, never
   null), so the generated TypeScript type has no `| undefined` on it.
2. Given an answer whose text is a refusal — for example `No explanation of "rerank off" is
   available.` — when `POST /ask` returns it, then `grounding` is `"declined"`, not `"grounded"`.
3. Given the refusal phrase list, when the code is read, then there is exactly one list, it is
   importable from one module, `evaluation/runner.py` and the live `/ask` path both use it, and it
   contains both English and German phrasings, including the `no explanation … is available` and
   `keine Erklärung … verfügbar` family.
4. Given an answer that is generated from passages and is not a refusal, when `POST /ask` returns
   it, then `grounding` is `"grounded"`.
5. Given `GET /interactions/{interaction_id}`, when it returns a stored ask, then the same
   `grounding` value is present, derived from the stored answer, so a reload renders the same badge;
   when it returns a stored `retrieval` interaction, then `grounding` is present and is
   `"no_relevant_sources"`, so the field is never null on either kind.
6. Given the Ask page, when `grounding` is `"grounded"`, then the answer card shows the **Grounded**
   badge and the source count exactly as today.
7. Given the Ask page, when `grounding` is `"declined"`, then the card shows a distinct **Not found in
   the documents** state instead of **Grounded**, and the header line no longer says
   "Generated from corpus".
8. Given the answer card's footer, when any answer renders, then the static
   "Evaluated with DeepFix RAG Engine" label is gone and the footer shows `N passages used` from
   `hits.length` instead, so no string in the card contains "Evaluated" and no label claims an
   evaluation, a score, or a judge that did not run.
9. Given the admin Hermes chat, when it renders, then it is unchanged: this story does not modify
   `web/src/features/admin-chat/admin-chat-page.tsx`, `web/src/features/admin-chat/chat-session.tsx`,
   or any module in `web/src/components/chat/`, and `git diff --stat -- web/src/components/chat/` is
   empty against the base commit.
10. Given `web/openapi.json` and `web/src/client/`, when `npm run generate:api` runs, then they are
    rewritten to include `grounding` and `git diff --exit-code -- web/openapi.json web/src/client`
    is clean against the committed tree.
11. Given the three required cases, when the suite runs, then an off-corpus refusal is not badged
    Grounded and a normal in-corpus answer is Grounded with sources.

## Out of scope
- Deciding grounding from retrieval scores. That is ASK-002's cutoff; this story classifies an answer
  that was already generated.
- Any change to the generation prompt or `generation.system_prompt`.
- Persisting `grounding` as a new column in the interactions table. It is derived from the stored
  answer on read, so an existing row needs no migration.

## Notes
- The phrase list currently lives in `src/deepfix_rag/evaluation/runner.py`. Moving it must keep
  `is_deflection` importable from `deepfix_rag.evaluation` (it is re-exported in
  `evaluation/__init__.py`) so no caller changes.
- `ApiModel` sets `json_schema_serialization_defaults_required`, so a field with a default still
  appears as required in the schema; `grounding` is given no default where the value is always known.
- The Ask page's controlled-mode props (`answer`, `hits`, `askedQuery`) are how `pages.test.tsx`
  drives it. `grounding` joins them as an optional prop with a default, so every existing call site
  still compiles and renders.

## Definition of done
- [ ] Code complete
- [ ] Schema and client regenerated and committed
- [ ] Tests added for the three required cases
- [ ] `uv run ruff check`, `uv run ruff format --check`, `bun run typecheck`, `bun run lint`,
      `bun run test` green

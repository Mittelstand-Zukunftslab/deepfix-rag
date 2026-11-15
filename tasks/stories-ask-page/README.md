# Ask page — story index

Stories for the three `/chat` defects found in the code review at `642f720`, produced with the
`create-story` skill and reviewed with the `validate-story` skill from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`.

Skill adaptations, stated once here so the files do not need to repeat them:

- The skill's default directory is `docs/stories/`. This repository keeps PRDs and stories in
  `tasks/`, so the stories live in `tasks/stories-ask-page/`, beside the PRD they trace to.
- The skill's id convention is project-wide `S-001…`. The review and the owner name these issues
  ASK-1, ASK-2 and ASK-6, so the `ASK-` prefix is kept and continues from the issue numbering
  rather than from `S-`. The numbers are project-unique, which is what the convention protects.
  They are not contiguous: ASK-3, ASK-4 and ASK-5 are review items outside this brief.
- One file per story, kebab-case slug, id inside the file: `ASK-001-grounding-status.md`.
- Each story has a sibling `*-validation.md` from `validate-story`.

## PRD

`tasks/prd-product-ui.md` is the PRD. US-010 ("Ask a question") owns the Ask screen, and FR-9 owns
what an interaction must store. No story here changes US-010's contract; each adds to it.

## Order

Stories are implemented one at a time, on `feat/hermes-optimizer`, each built, tested and committed
before the next starts.

| # | Id | Title | Depends on |
| :--- | :--- | :--- | :--- |
| 1 | ASK-001 | Say what grounding an answer actually has | — |
| 2 | ASK-002 | Drop passages that are not relevant to the question | ASK-001 |
| 3 | ASK-006 | Keep pending state with the session that asked | — |

ASK-006 shares `chat-page.tsx` with ASK-001 but not the same regions, and its own outcome is the
session boundary. It is ordered last so the badge work lands first on a page whose turn model has
not moved yet.

No cycles: the graph above is a topological order. ASK-002 depends on ASK-001 because the new
"nothing relevant" outcome is one of the grounding states ASK-001 introduces; ASK-001 alone can
report it only for an answer that was generated and came back declined.

## Fixed decisions (do not re-open in a story)

- The Ask page is a **stateless single-turn** screen against `POST /ask`. It is not the admin Hermes
  chat, which has its own thread store and streaming transport. The two share
  `web/src/components/chat/` and nothing else, and the shared components are not to change
  behaviour for the admin chat's benefit.
- Grounding is decided by the **backend**, not by string-matching in the browser. The UI renders one
  of three states; it does not re-derive them.
- The relevance floor is **configuration** (`rerank.min_relevance_score` under `RAGConfig`), not an
  environment variable, so the optimizer can tune it with a plain `--set`.
- The deflection phrase list is **shared**, not duplicated: the eval runner and the live path read
  the same list.
- The generated client is regenerated from the FastAPI schema with `npm run generate:api`, and the
  committed `web/openapi.json` and `web/src/client` must match what that command writes.

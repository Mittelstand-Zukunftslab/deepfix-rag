# Ask chat — story index

Stories for `tasks/prd-ask-streaming-multiturn.md`, produced with the `create-story` skill and reviewed with the
`validate-story` skill from `https://github.com/Mittelstand-Zukunftslab/awesome-skills` (the repository's closest
available names; there is no `create-stories` or `validate-stories`, so one file per story is the unit either way).

Skill adaptations, stated once here so the files do not need to repeat them:

- The skill's default directory is `docs/stories/`. This repository keeps PRDs and stories in `tasks/`, so the
  stories live in `tasks/stories-ask-chat/`, beside the PRD they trace to — the same arrangement as
  `tasks/stories-ask-page/` and `tasks/stories-admin-hermes-chat/`.
- The skill's id convention is project-wide `S-001…`. The PRD already assigns `ASKCHAT-001…ASKCHAT-011` and its
  "Story to FR map" is keyed on those ids, so the PRD ids are kept. They are project-unique and do not collide with
  `ASK-001…006` (the Ask page defects) or `CHAT-001…023` (the admin Hermes chat).
- The skill calls for a `## Type`, `## Status`, `## Summary`, `## PRD traceability`, `## Acceptance criteria`,
  `## Out of scope`, `## Notes`, `## Definition of done` file. Each file here has those, plus a `## Dependencies`
  section, which `validate-story` asks for as a check even though the template omits it.
- Each story has a sibling `*-validation.md` from `validate-story`.

## PRD

`tasks/prd-ask-streaming-multiturn.md` is the PRD, and its validation report is
`tasks/prd-ask-streaming-multiturn-validation.md`. The PRD carries all eleven decisions (D-1 … D-11); no story here
reopens one.

## What this feature is

The Ask page becomes a streaming, multi-turn chat, and the DeepFix RAG becomes a tool the model calls. The agent loop
runs in FastAPI, not in the web server, and streams the Vercel AI SDK's UI message stream — the protocol the admin
Hermes chat already speaks.

## Order

Stories are implemented one at a time, on `feat/hermes-optimizer`, each built, tested and committed before the next
starts. The order below is a topological sort of the dependencies; there are no cycles.

| # | Id | Title | Depends on |
| :--- | :--- | :--- | :--- |
| 1 | ASKCHAT-001 | Keep conversations on the server, private to their owner | — |
| 2 | ASKCHAT-002 | Give the model one read-only tool over the champion index | ASK-001, ASK-002 (landed) |
| 3 | ASKCHAT-003 | Stream the answer as the AI SDK UI message stream | ASKCHAT-001, 002 |
| 4 | ASKCHAT-004 | Stop, resume, regenerate, and fail honestly | ASKCHAT-003 |
| 5 | ASKCHAT-005 | Bound one member and one deployment, and plumb every bound | ASKCHAT-003, 004 |
| 6 | ASKCHAT-006 | One typed web data layer and one Chat per thread | ASKCHAT-001 … 005 |
| 7 | ASKCHAT-007 | Make the Ask page a streaming, stoppable conversation | ASKCHAT-006 |
| 8 | ASKCHAT-008 | Show the evidence, follow its citations, and say how grounded it is | ASKCHAT-007, 003 |
| 9 | ASKCHAT-009 | Bring the browser's existing conversations across, once | ASKCHAT-001, 006 |
| 10 | ASKCHAT-010 | Trace the tool calls and export the standalone question | ASKCHAT-003, 004 |
| 11 | ASKCHAT-011 | Prove it on the deployed stack | ASKCHAT-001 … 010, a deployment |

ASKCHAT-009 is the only Should story and is ordered ninth rather than last because it depends on the client layer, not
on tracing. If it is cut, the fallback is in the PRD's D-5: delete the legacy keys with a one-time notice, so no member
is left on a store the server cannot see. ASKCHAT-011 is a Chore and an operator session; a cloud agent cannot run it.

## Fixed decisions (do not re-open in a story)

These are the eleven PRD decisions and the owner's four answers of 2026-11-15, restated in the form a story needs.

- **The loop runs in deepfix-api** and streams the AI SDK UI message stream (D-1). The browser never holds a model
  key or a model id, and the BFF adds the identity bearer as it does for every `/api/*` call.
- **`search_documents` is the only tool, it is read-only, and a turn always retrieves** (D-2). `tool_choice:
  "required"` on the first step, and if the model produces no call the server runs the tool itself. At most
  `ASK_CHAT_MAX_TOOL_CALLS` calls; the last permitted step is sent with `tool_choice: "none"`.
- **Grounding is ASK-001's enum plus a validated citation list** (D-3). `grounding.Grounding` and
  `grounding.grounding_for` are the only classifier; no story adds a fourth status or a second phrase list.
- **Stop cancels the model stream, not just the wait** (D-4). `CustomLiteLLM.achat` is `asyncio.to_thread`, so the
  loop calls `litellm.acompletion(stream=True)` directly and closes the generator.
- **Threads are server-side and per member** (D-5), and `interactions` keeps `kind='ask'` with two nullable columns
  rather than a new kind, because the `CHECK` cannot be altered in place.
- **Served retrieval equals scored retrieval** (D-6). The tool reads the champion config through
  `QueryService.retrieve`; `deepfix eval` and the gate are unchanged and single-turn eval remains valid.
- **The chat resolves `LLM_MODEL`** — the owner's answer, and there is no `ASK_CHAT_MODEL` (D-7). `qwen/qwen3.8-27b`
  is only a documented manual fallback via `LLM_MODEL`, as one comment in `example.env`. ASKCHAT-005 guards this with
  a test.
- **The limits are D-8's numbers**, in `Settings`, in the `backend-api` Compose environment with non-empty defaults,
  and in `example.env`. No new container and no cap change.
- **A thread delete keeps the `interactions` rows and the votes** — the owner's answer (D-9). The thread and its turns
  go; the feedback the optimizer reads stays.
- **A grounded but uncited answer shows as Partly grounded** — the owner's answer (D-10). It extends the landed
  `GROUNDING_BADGE` / `groundingNote` copy with one rendered state over three stored values.
- **Threads are unbounded, with a 50-item list cap and no prune** — the owner's answer (D-11). The 50 is a display
  cap, not a lifecycle rule.

## What has already landed

The stories build on three Ask fixes that are on `feat/hermes-optimizer` at `223a122`, and none of them re-opens or
re-implements any of it:

| Story | Landed | What the stories consume |
| :--- | :--- | :--- |
| ASK-001 | `97aef60` | `src/deepfix_rag/grounding.py` — `Grounding` (`grounded` / `declined` / `no_relevant_sources`), `DEFLECTION_PHRASES`, `DEFLECTION_PATTERNS`, `grounding_for`; a required `grounding` field on `AskResponse` and `InteractionResponse`; `GroundingBadge` + `groundingNote` in `web/src/features/chat/` |
| ASK-002 | `00aba89` | `RAGConfig.rerank.min_relevance_score` (0.2, set in `configs/champion.yaml`), `RAGWorkflow._ranker_split` / `split_by_relevance_floor`, `QueryService._search_split`, `RAGResult.dropped_results` |
| ASK-006 | `223a122` | `pendingBySession: Record<sessionId, query>` in `chat-page.tsx`, so a story that keeps per-session state keeps this shape rather than reintroducing a page-level flag |

## Tests

Only meaningful integration and end-to-end tests count as a story's evidence. A narrow unit test of a helper is not
acceptance evidence, and no story asks for one.

On the Python side that means a real SQLite file, the real FastAPI app built through `create_app` against `tmp_path`,
and a real local OpenAI-compatible HTTP endpoint — no `unittest.mock`, `Mock`, `AsyncMock`, or `monkeypatch`, per
`AGENTS.md`.

On the web side the repository's established practice is what the stories name: the real component, the real
Assistant UI runtime and the real AI SDK transport, with `vi.stubGlobal("fetch", …)` speaking frames copied from a
real streamed response. That is the transport boundary, not a stub of domain logic, and it is how
`web/src/test/admin-chat-session.test.tsx` already proves the admin chat. A story that stubbed the transport's own
parser would be testing the stub.

Each story names its required test in an acceptance criterion, so the tester can map a check to a line. The commands
are the repository's standard ones:

```bash
uv run --env-file .env pytest -v -m "not network and not e2e"
cd web && bun run typecheck && bun run lint && bun run test && bun run build
```

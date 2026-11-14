# Admin Hermes chat — story index

Stories for `tasks/prd-admin-hermes-chat.md`, produced with the `create-story` skill and reviewed with the `validate-story` skill from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills` (closest available names: `create-story`, `validate-story`; the repo has no
`create-stories` or `validate-stories`).

Skill adaptations, stated once here so the files do not need to repeat them:

- The skill's default directory is `docs/stories/`. This repository keeps product documents in `tasks/`, so the stories live in
  `tasks/stories-admin-hermes-chat/`, next to the PRD.
- The skill's id convention is project-wide `S-001…`. The PRD already assigns `CHAT-001…CHAT-012` and its "Story to FR map" is keyed on
  those ids, so the PRD ids are kept. They are project-unique (no `CHAT-*` id exists elsewhere in `tasks/`), which is what the convention
  protects.
- One file per story, kebab-case slug, id inside the file: `CHAT-001-chat-profile-standalone-gateway.md`.
- Each story has a sibling `*-validation.md` from `validate-story`.

## Order

Stories are implemented one at a time, on `feat/hermes-optimizer`, each tested and committed before the next starts.

| # | Id | Title | Depends on |
| :--- | :--- | :--- | :--- |
| 1 | CHAT-001 | Start the chat profile as a gated standalone gateway | — (optimizer PRD US-001…US-010 in tree) |
| 2 | CHAT-002 | Chat store and audit tables | CHAT-001 |
| 3 | CHAT-003 | The streaming proxy route | CHAT-001, CHAT-002 |
| 4 | CHAT-004 | Server prompt and status digest | CHAT-002, CHAT-003 |
| 5 | CHAT-005 | Threads, resume, and cancel | CHAT-002, CHAT-003 |
| 6 | CHAT-006 | Rate limit and audit read | CHAT-003, CHAT-005 |
| 7 | CHAT-007 | Action cards | CHAT-002, CHAT-003, CHAT-006 |
| 8 | CHAT-008 | Run progress for a thread | CHAT-002, CHAT-007 |
| 9 | CHAT-009 | Web data layer for admin chat | CHAT-003…CHAT-008 (all routes exist) |
| 10 | CHAT-010 | The chat UI in the Admin section | CHAT-009 |
| 11 | CHAT-011 | Chat tracing in MLflow | CHAT-001, CHAT-004 |
| 12 | CHAT-012 | Deployed verification on Dokploy | CHAT-001…CHAT-011, deployment |

No cycles: the graph above is a topological order, and CHAT-011 sits late only because it needs the prompt sentence CHAT-004 introduces.

## Sizing

`check-story-size`'s heuristic would call several of these "too large" by AC count (CHAT-001 has 18, CHAT-003 has
22, CHAT-010 has 20), because it assumes a story is a single feature slice. Here the split is fixed elsewhere:

- The PRD already defines twelve stories, each "one implementation session on `feat/hermes-optimizer`,
  independently testable, and committed on its own", and its "Story to FR map" and phased milestones are keyed on
  those ids. Splitting one here would break that mapping without adding a testable outcome.
- The large AC counts are dense checklists over one coherent change, not several features. CHAT-001 is "make the
  profile start and prove it" plus the plumbing that makes the rest reachable; CHAT-003 is one route with its gate,
  its limits, its relay, and its error contract; CHAT-010 is one page.
- Where a story genuinely risked doing two things at once, the split has been made instead: the gate predicate ships
  in CHAT-001 and is applied to routes in CHAT-003; `chat_enabled` on the optimization payload sits in CHAT-010 with
  its pytest case rather than in a story of its own.

The one real risk of this shape is CHAT-003's test surface, which is the place an implementer is most likely to run
out of a session. If that happens, the honest split is relay-and-gate first, error framing second, keeping the
`POST /admin/chat/completions` operation id in the first half.


## Fixed architecture (do not re-open in a story)

- The browser calls one admin-only route on deepfix-api, `POST /admin/chat/completions`, OpenAI-shaped, answered with SSE. It reaches that
  route through the existing same-origin `/api` proxy on the web server.
- deepfix-api forwards to `HERMES_CHAT_BASE_URL` (default `http://hermes:8643`) on the internal Compose network and injects
  `HERMES_CHAT_API_SERVER_KEY` server-side. Hermes is never public: no published port, no public route, no dashboard.
- Long work goes through the existing job API (`start_optimization_run`, `cancel_optimization_run`, `optimization_runs`,
  `optimization-logs/<run_id>.log`). A chat turn never performs a job.
- Action cards are the only write path, and only the admin's confirmation executes one.
- Holdout (`feedback.is_holdout`) and `apply_candidate` stay in the backend and are unreachable from chat.
- Chat is off by default (`HERMES_CHAT_ENABLED=false`) and, when off, runs no gateway process and binds no port.
- No model id is hardcoded in Python or TypeScript. `HERMES_CHAT_MODEL` falls back to `HERMES_MODEL`.
- The Hermes secret scrub (`optimization._secrets` / `_redact`, extended with the chat key) stays in force on every outbound string.

## Open questions

The PRD's three remaining open questions all have an implemented default, so none blocks a story. Each is carried by
exactly one story; a reader should not expect a fourth decision document.

| Open question | Default the stories implement | Where |
| :--- | :--- | :--- |
| 1. Does Hermes abort an in-flight chat turn when the client disconnects? | Close the upstream stream, record `cancelled`, keep the partial text, discard the rest. Measured during CHAT-001 and recorded in its verification note. | CHAT-001 (measurement), CHAT-005 AC 8 |
| 2. Should the chat page one run's step log on demand? | No. The 50-line tail answers "what is it doing now"; a paged route would be an additive story. | CHAT-008 (out of scope) |
| 3. May a confirmed start run while a chat turn is still streaming? | Yes. The conversation plane and the job plane are separate, and `start_optimization_run` already refuses a second concurrent run. | CHAT-007 AC 10 |

Two questions need Fadel rather than a story:

1. **Audit retention.** The PRD keeps `admin_chat_audit` unbounded (assumption A6). At tens of rows per admin per day
   this is fine for the pilot, but nothing in these stories prunes or expires a row, so the first decision is
   "leave it" versus "add a retention job", not a design change. CHAT-006 lists retention as out of scope and points
   here.
2. **Whether a thread delete should erase audit rows too.** D-4 keeps them and says so plainly, naming the one-line
   change if the stricter reading should win. Every story here implements D-4 as written (CHAT-002, CHAT-005,
   CHAT-006, CHAT-010's dialog copy). If Fadel reverses it, four stories change together, not one.

## Test commands

Python, from the repository root:

```bash
uv run --env-file .env pytest -v -m "not network and not e2e"
uv run --env-file .env pytest -v tests/test_hermes_sandbox.py tests/test_hermes_chat_profile.py
uv run --env-file .env pytest -v -m api
uv run ruff check
uv run ruff format --check
```

Web, from `web/` (CI uses bun; see `.github/workflows/ci.yml`):

```bash
bun install --frozen-lockfile
npm run generate:api          # API stories only; writes web/openapi.json and web/src/client
bun run typecheck
bun run lint
bun run test
bun run test src/test/admin-chat-stream.test.ts
```

Focused, when a full run is slow:

```bash
uv run --env-file .env pytest -v tests/test_admin_chat.py
uv run --env-file .env pytest -v tests/test_admin_chat_routes.py
uv run --env-file .env pytest -v tests/test_api_optimization.py
```

## FR coverage

Every FR is claimed by at least one story; CHAT-012 verifies the whole set end to end.

| Story | FRs |
| :--- | :--- |
| CHAT-001 | FR-001, FR-002, FR-020, FR-029 |
| CHAT-002 | FR-010, FR-016, FR-018, FR-021 |
| CHAT-003 | FR-003, FR-004, FR-005, FR-007, FR-019, FR-021, FR-028 |
| CHAT-004 | FR-006 |
| CHAT-005 | FR-008, FR-009, FR-010, FR-011, FR-012 |
| CHAT-006 | FR-017, FR-018 |
| CHAT-007 | FR-013, FR-014, FR-015 |
| CHAT-008 | FR-016 |
| CHAT-009 | FR-003, FR-022 |
| CHAT-010 | FR-022, FR-023, FR-025, FR-026, FR-027, FR-028 |
| CHAT-011 | FR-024 |
| CHAT-012 | all, verified end to end |

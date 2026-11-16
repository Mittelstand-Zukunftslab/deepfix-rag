# Contributing

[`AGENTS.md`](AGENTS.md) is the full guide: the architecture, the layer rules, the Python and TypeScript conventions, the test markers, and the commit rules. Read it before your first change. This page is the short version.

## Branches

- `feat/hermes-optimizer` is the integration branch. Cut every feature branch off it, and keep it up to date by rebasing rather than merging it into your branch.
- Never commit to `main` or `dev`, and do not force-push a branch other people are working on.
- One branch per story, named for the story: `CHAT-006-audit-prune`, `ASK-004-citations`.

## Commits

- One user story per commit, and one logical change per commit. A review should be able to read a single commit and a broken story should be revertible on its own.
- Start the message with the story or area, then say what changed: `CHAT-006: bound the chat, expose the audit trail, and prune it hourly`.
- Never commit a secret, a real `.env`, an index, a dataset, or a generated run artifact. `example.env` carries key names and defaults only.
- Adding a secret means adding its name to all three scrub lists in the same commit: `optimization._secrets` / `_redact`, `docker/hermes/deepfix_run_tag`, and `docker/hermes/bin/deepfix`.

## Pull requests

- Push your branch, then open a pull request against `feat/hermes-optimizer`. Say which story it closes, what you changed, and what you ran.
- A pull request is ready for review when `make lint` and the fast suite pass locally: `uv run --env-file .env pytest -v -m "not network and not e2e"`.
- Do not merge your own pull request; do not enable auto-merge.
- Changing an API route or a Pydantic model means regenerating `web/openapi.json` and `web/src/client/` with `bun run generate:api` from `web/`, because CI regenerates both and fails if they differ from what you committed. Never edit them by hand.

## Tests

Write tests for behaviour a user or an operator depends on, and skip the ones that only restate a constant.

- **Integration and end-to-end coverage is the default.** A new domain invariant, a new route, or a new CLI contract gets a test that exercises it against real code and deterministic local fixtures.
- **No mocks of domain logic.** No `unittest.mock`, `Mock`, `AsyncMock`, or `monkeypatch` around the pipeline, the store, or a loader. If a test needs a live service, mark it `network` and let it be deselected by default.
- **Every module declares its marker** (`unit`, `cli`, `integration`, `e2e`, `network`, `api`) because the suite runs with `--strict-markers`. Reuse the fixtures in `tests/conftest.py`, and write every file a test produces under `tmp_path`.
- Marking a case `network` keeps it out of CI. It does not keep it out of a command that names `network` or `e2e`, or a command pointed at its file; leave that behaviour in place.
- A change to `web/` needs `bun run typecheck && bun run lint && bun run test` from `web/` to pass.

## Before you push

```bash
uv run ruff check --fix
uv run ruff format
uv run --env-file .env pytest -v -m "not network and not e2e"
cd web && bun run typecheck && bun run lint && bun run test   # only if web/ changed
```

Update the docs your change invalidates, and document environment **key names**, never values.

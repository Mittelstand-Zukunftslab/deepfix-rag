# DeepFix docs

Operator-facing documents. Product planning lives in `tasks/`; the front door is `README.md`.

| Document | Use it when |
| :--- | :--- |
| [`deployment.md`](deployment.md) | Deploying to Dokploy: memory caps, `--no-deps` rebuilds, auto-deploy off, domains, secrets. |
| [`admin-hermes-chat-verification.md`](admin-hermes-chat-verification.md) | Verifying the admin Hermes chat on a deployed stack, with chat off and with chat on. |
| [`admin-chat-interface-review.md`](admin-chat-interface-review.md) | The `better-interface` review of the redesigned chat: findings, what was rejected, and what is still unverified. |
| [`ask-chat-verification.md`](ask-chat-verification.md) | The operator runbook for the streaming Ask chat: what to check on a deployed stack, how, and what to record. Prepared but not yet run. |

Reference material elsewhere:

- `README.md` — what DeepFix is, installation, CLI, configuration, the optimizer, the chat, MLflow tracing, and the MLflow/RustFS lockdown.
- `AGENTS.md` — how work is planned, committed, and tested.
- `configs/README.md` — every shipped configuration file and when to use it.
- `tasks/` — PRDs, stories, plans, and implementation notes.

There is no generated API reference here. `GET /docs` on a running API serves the live OpenAPI schema, and `web/openapi.json` is the committed copy the web client is generated from.

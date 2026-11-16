# DeepFix docs

Operator-facing documents. Product planning lives in `tasks/`; the front door is [`../README.md`](../README.md).

| Document | Use it when |
| :--- | :--- |
| [`admin-mode.md`](admin-mode.md) | Walking through History, Optimization, and Hermes chat, with stills from the [admin mode tour](media/deepfix-admin-tour.mp4). |
| [`deployment.md`](deployment.md) | Deploying to Dokploy: memory caps, `--no-deps` rebuilds, auto-deploy off, domains, secrets. |
| [`admin-hermes-chat-verification.md`](admin-hermes-chat-verification.md) | Verifying the admin Hermes chat on a deployed stack, with chat off and with chat on. |
| [`admin-chat-interface-review.md`](admin-chat-interface-review.md) | The `better-interface` review of the redesigned chat: findings, what was rejected, and what is still unverified. |

## Media

| File | What it is |
| :--- | :--- |
| [`media/deepfix-admin-tour.mp4`](media/deepfix-admin-tour.mp4) | 104-second recording of the live admin mode, captions burned in. 1440×900, no audio. |
| [`media/deepfix-admin-tour.srt`](media/deepfix-admin-tour.srt) | The same cues as a subtitle file; its timings are the chapter list in the README. |
| [`media/deepfix-admin-tour-preview.gif`](media/deepfix-admin-tour-preview.gif) | 1.9 MB animated preview for the README, sampled at 2 fps. |
| `media/admin-tour-*.png` | Stills at full resolution, with the caption bar, used by `admin-mode.md`. |

The recording was made against a stack with a signed-in test account, so the account email in the header and the sidebar, and the email column in the History table, are blurred in the MP4, in the GIF, and in every still. The burned-in captions are untouched.

The stills were cut from the captioned recording at the cue boundaries, so each one shows the caption for the segment it illustrates.

## Elsewhere

- [`../README.md`](../README.md) — what DeepFix is, the architecture, quick start, configuration, the CLI, the optimizer, the chats, and the MLflow/RustFS lockdown.
- [`../AGENTS.md`](../AGENTS.md) — how work is planned, committed, and tested.
- [`../configs/README.md`](../configs/README.md) — every shipped configuration file and when to use it.
- [`../tasks/`](../tasks/) — PRDs, stories, plans, and implementation notes.

`ask-chat-verification.md` lives on `feat/ask-chat-streaming` (PR #23) until that branch lands, so it has no link here yet. It is the operator runbook for the streaming Ask chat and is currently marked *prepared, not run*.

There is no generated API reference here. `GET /docs` on a running API serves the live OpenAPI schema, and `web/openapi.json` is the committed copy the web client is generated from.

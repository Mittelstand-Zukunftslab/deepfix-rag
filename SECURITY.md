# Security policy

## Reporting a vulnerability

Report it privately through GitHub's security advisories: open the repository's **Security** tab, choose **Advisories**, then **Report a vulnerability**. That private draft is visible only to you and the maintainers, and it is the channel for anything that could be exploited before a fix ships.

Do not open a public issue, a pull request, or a discussion for a suspected vulnerability, and do not paste an exploit, a token, a session cookie, or a customer document into one. If you are unsure whether something is sensitive, report it privately and let the maintainers decide.

Please include what you can: the affected route or subcommand, the configuration it needs, the steps that reproduce it, and the impact you believe it has. A proof of concept against a local stack is welcome; a live deployment is not, so do not test against a host you do not own.

## What to expect

The maintainers acknowledge a report in the advisory thread, ask for anything missing there, and coordinate a fix and a disclosure date with you. Credit is given in the advisory unless you ask otherwise.

## Scope

This repository ships an API, a web app, a CLI, and an optimizer sandbox; the deployment environment is in scope too. Please look especially at:

- **Authentication and roles.** Every `/admin/*` route needs the `admin` role, checked in the API and again in the web app. A member reaching admin data, or a signed-out visitor reaching any data, is a vulnerability.
- **The identity token.** The web app proxies `/api/*` to FastAPI with a short-lived Ed25519 token; forging one, or replaying it after expiry, is a vulnerability.
- **The Hermes sandbox.** It has no Docker socket and no terminal toolset in the chat profile, and its object-store key is limited to `optimization/runs/*`. Reaching anything outside those bounds is a vulnerability.
- **The secret scrub.** `optimization._secrets` / `_redact` removes configured secrets from step lines, heartbeats, terminal output, and stored reasons before they reach a log, a database column, or a chat reply. A secret that survives it is a vulnerability.
- **Path traversal and uploads.** The Library accepts 20 MB text, Markdown, and PDF uploads into the shared workspace corpus.

Out of scope: findings that need a compromised host or a leaked deployment secret, a denial of service from a synthetic load on a single-machine deployment, and anything that only affects a configuration the docs already call unsupported.

## Secrets in this repository

No secret belongs in this repository, in an issue, or in a pull request: not a key, not a password, not a filled `.env`, not a dataset that was never meant to be public. `example.env` and the Compose files carry key names and defaults only. If you spot a committed credential, report it privately through the advisory flow rather than in an issue, and rotate it.

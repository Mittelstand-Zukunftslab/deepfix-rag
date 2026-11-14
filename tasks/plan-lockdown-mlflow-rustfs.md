# Plan: lock down MLflow and RustFS

Status: plan only. Nothing here is implemented. Every change is described so it
can be applied to the Dokploy-edited compose on the server without a full-stack
redeploy.

## What is exposed today

Both services are reachable from the internet in two independent ways: a
published host port on the compose service, and a Dokploy/Traefik domain.

| Service | Compose port | Public hostname | Auth |
| :--- | :--- | :--- | :--- |
| MLflow UI + API | `5000:5000` | `deepfix-mlflow.mittelstand-zukunftslab.de`, `mlflow.mittelstand-zukunftslab.de`, `mlflowhpi.mittelstand-zukunftslab.de` | None |
| RustFS S3 API | `9000:9000` | `hpi-s3.mittelstand-zukunftslab.de` | S3 signature (IAM keys) |
| RustFS console | `9001:9001` | none found in the repo | None known |

`docker-compose.yml` publishes all of these. The hostnames come from Dokploy's
domain settings and from `.env` / `example.env`, not from a Traefik config in
this repository — there is no `traefik.*` label anywhere in the repo, so the
routing lives entirely in Dokploy.

### What is already safe

- Neither service holds the product database.
- The Hermes S3 key is `hermes-optimizer`, scoped by IAM to
  `optimization/runs/*` (`src/deepfix_rag/hermes_iam.py`). It is not the root
  key, and the root key is not in the Hermes environment.
- MLflow's `MLFLOW_SERVER_ALLOWED_HOSTS` limits which `Host` header it answers
  for. It is a Host-header allow-list, **not** authentication: anyone who sends
  the right `Host` gets the UI.

### What is actually at risk

1. **MLflow is unauthenticated.** Anyone who finds the domain can read every
   experiment, every logged parameter, and every eval table. `FR-16` in the
   Hermes PRD says secrets must not appear in MLflow params; the redaction is
   the only thing standing between a logged value and a public page. The UI is
   also writable — experiments, runs, and tags can be deleted or altered.
2. **The RustFS console is exposed on `9001`.** If it is reachable and the
   console has default or weak credentials, that is full object-store control.
3. **The S3 API is public.** It is signature-protected, so this is the least
   urgent, but every request from Hermes and the API currently leaves the
   Docker network and comes back in through the public hostname, which adds
   latency and a public attack surface for credential-stuffing and
   enumeration.

## Who actually needs these, and from where

This is what decides which option is viable.

### MLflow

| Consumer | Path today | Needs to change? |
| :--- | :--- | :--- |
| `backend-api`, `hermes`, `diagnostic-agent` | `MLFLOW_TRACKING_URI` over the Compose network | No — already internal (`http://mlflow:5000` in `example.env`) |
| Admin UI "open run in MLflow" link | Built server-side by `mlflow_run_url()` from `MLFLOW_TRACKING_URI` and rendered as an `<a href>` in `web/src/features/admin/admin-page.tsx:281` | **Depends on the value.** `.env` sets `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`, which is a dead link in a browser. On the server, if it is the public domain, that link is the reason the domain is public. |
| The owner reading MLflow by hand | The public domain | This is the real requirement to satisfy |
| OTLP trace ingest | Not used by the Hermes plugin | The plugin posts to `MLFLOW_TRACKING_URI` (`sandbox_boot.py:619`), so it follows whatever that is set to. `.env`'s separate `MLFLOW_OTLP_ENDPOINT` is not read by the Hermes image. |

So exactly one consumer outside the Docker network needs MLflow: a human
opening the UI. Everything machine-to-machine is already internal.

### RustFS

| Consumer | Path | Needs to change? |
| :--- | :--- | :--- |
| `backend-api` (run bundles, index snapshots) | `S3_ENDPOINT_URL` | Should be internal |
| `hermes` (`optimization/runs/*`) | `S3_ENDPOINT_URL` | Should be internal |
| LanceDB in both | same | Should be internal |
| Human uploading/downloading by hand | console or a CLI | Optional |

`.env` sets `S3_ENDPOINT_URL=https://hpi-s3.mittelstand-zukunftslab.de`, i.e.
production traffic goes out to the public hostname and back. Nothing requires
that.

## Options

### MLflow

#### Option A — Internal only, no public domain

Remove the Dokploy domains and the `5000:5000` publish. Nothing outside the
Compose network reaches MLflow.

- **For:** smallest attack surface; MLflow has no auth of its own, so not
  exposing it is the only way to not rely on one. No new component.
- **Against:** the owner loses the UI. The admin "open run" link must become a
  relative API route or be dropped. Reading a run by hand means `ssh` plus
  `curl http://mlflow:5000` or a local tunnel.
- **Changes:** delete the three domains in Dokploy; drop the `ports:` entry;
  set `MLFLOW_TRACKING_URI=http://mlflow:5000` everywhere; change
  `mlflow_run_url()` to return `None` or a DeepFix-owned proxy path.
- **Rollback:** re-add the domain in Dokploy and the `ports:` line. One step,
  no data change.

#### Option B — Keep a public domain, put Traefik basic-auth in front

Keep one domain (retire the other two), and attach a Traefik middleware so the
UI asks for a username and password before it proxies.

- **For:** the owner keeps a bookmark; one credential to manage; no change to
  how the API reaches MLflow internally.
- **Against:** basic-auth over TLS is only as good as the password (fine for
  one human, not for a team). The MLflow **artifact** endpoints and the API
  endpoints are behind the same host, so a script that used the public domain
  for programmatic access needs the credential too — which means a credential
  in a config file. `MLflow` 3.x also serves `/ajax-api/...` and the UI's
  static assets, so the middleware must cover the whole path, not just `/`.
- **Changes:** in Dokploy, one domain on `mlflow`, middleware
  `basicAuth` with a generated htpasswd; keep `ports:` off and let Traefik
  reach the container on the internal network. `MLFLOW_SERVER_ALLOWED_HOSTS`
  and `CORS_ALLOWED_ORIGINS` keep the public hostname. The repo cannot express
  this: there is no Traefik label or file in the repository, so this is a
  Dokploy-only change.
- **Rollback:** remove the middleware from the router. The domain stays.
- **Caveat:** the middleware is Dokploy-managed. It is invisible in this repo,
  so a future `docker-compose.yml` that re-adds `ports: 5000:5000` silently
  bypasses it. The repo-side change must be the port removal, and the
  middleware is defence in depth.

#### Option C — Public domain with forward-auth (Authentik/OIDC)

Same shape as B but delegated to an identity provider.

- **For:** real accounts, MFA, audit; matches the Better Auth identity already
  in the web app.
- **Against:** a new service to run and keep healthy; more moving parts than
  one operator needs; a misconfigured forward-auth that fails open is worse
  than no auth.
- **Changes:** an Authentik (or similar) service plus a Dokploy middleware.
- **Rollback:** remove the middleware.

### RustFS

#### Option D — Console off, S3 API internal only

Set `RUSTFS_CONSOLE_ENABLE=false`, remove the `9001:9001` publish, remove the
`9000:9000` publish, and point `S3_ENDPOINT_URL` at `http://rustfs:9000` in
every service. Drop the `hpi-s3` public domain.

- **For:** the console is the highest-risk surface and it has no consumer in
  this repo. The S3 API is used only from inside the Compose network, so
  removing its public route costs nothing and removes an internet-facing
  credential endpoint.
- **Against:** the owner cannot use the console or a local S3 client against
  the public hostname. Hand-inspection needs `ssh` plus `curl` to
  `http://rustfs:9000`, or a temporary tunnel.
- **Changes:** `RUSTFS_CONSOLE_ENABLE: "false"`, remove both `ports:` entries,
  set `S3_ENDPOINT_URL=http://rustfs:9000` in `.env` (and therefore in every
  service that already reads it), remove the Dokploy domain. Keep
  `S3_URI=s3://deepfix-index/...` unchanged — the URI is bucket/key, not a
  host.
- **Rollback:** re-add `ports` and the domain, flip the console flag back.
  Data is untouched: it lives in the `rustfs_data` volume.

#### Option E — Keep the S3 API public, console off

Only disable the console and leave the S3 API reachable.

- **For:** nothing in the repo needs to change for the API; an external S3
  client keeps working.
- **Against:** leaves a public, signature-protected endpoint whose only users
  are inside the network anyway. The main win (removing the console) is the
  same as D.
- **Changes:** `RUSTFS_CONSOLE_ENABLE: "false"`, remove `9001:9001`.
- **Rollback:** re-enable the flag.

#### Option F — Firewall the ports instead of changing compose

Leave compose alone and block `5000`, `9000`, `9001` at the host firewall.

- **For:** no compose edit; fastest to apply.
- **Against:** does not remove the Dokploy domain, which is a separate ingress
  path. A port filter that also breaks the Dokploy router is easy to get
  wrong, and the compose file keeps advertising the exposure, so the next
  person re-opens the hole. Fragile as the primary control; useful only as
  defence in depth.

## Recommendation

Apply **Option D for RustFS** and **Option B for MLflow**, with Option A as the
fallback if the owner does not want to manage a credential.

Rationale, in order of risk removed per unit of work:

1. **RustFS console off and both RustFS ports closed (D).** The console has no
   consumer in this repository and is the only unauthenticated-adjacent,
   full-control surface. Closing the S3 port costs nothing because
   `S3_ENDPOINT_URL` already has an internal form and every caller is a
   container. This one change removes the largest risk.
2. **MLflow keeps one domain behind Traefik basic-auth (B).** The owner
   genuinely reads the UI, so Option A removes a real capability. Basic-auth
   satisfies that with one credential and no new service. Retire the other two
   MLflow domains so there is one door, not three.
3. If the owner prefers zero credentials to manage, use **A** and accept
   reading MLflow over `ssh` + `curl` or a local tunnel. Do not run MLflow on
   a public domain with no auth in either case.

Do **not** choose C now: a forward-auth service is more operational surface
than one operator reading one dashboard justifies.

### Exact changes to mirror on the server (Dokploy-edited compose)

Server-side, in the compose Dokploy actually runs:

```yaml
# mlflow
-  ports:
-    - "5000:5000"

# rustfs
-  ports:
-    - "9000:9000" # S3 API
-    - "9001:9001" # Console
   environment:
     RUSTFS_ADDRESS: ":9000"
-    RUSTFS_CONSOLE_ENABLE: "true"
+    RUSTFS_CONSOLE_ENABLE: "false"
```

Dokploy UI:

- MLflow: keep **one** domain, delete the other two, attach a Traefik
  `basicauth` middleware to the survivor.
- RustFS: delete the `hpi-s3` domain.

`.env` on the server:

```dotenv
- MLFLOW_TRACKING_URI=https://deepfix-mlflow.mittelstand-zukunftslab.de
+ MLFLOW_TRACKING_URI=http://mlflow:5000
- S3_ENDPOINT_URL=https://hpi-s3.mittelstand-zukunftslab.de
+ S3_ENDPOINT_URL=http://rustfs:9000
```

Keep `S3_URI`, `S3_REGION`, and the IAM keys as they are.

Two consequences to decide before applying:

- **`mlflow_run_url()`** builds the admin "open run" link from the
  server-side `MLFLOW_TRACKING_URI`. With the internal value it becomes
  `http://mlflow:5000/...`, which a browser cannot open. Either leave it
  pointing at the public domain name only for link-building (a second setting,
  e.g. `MLFLOW_UI_BASE_URL`) or have it return `None` and let the UI hide the
  link. This plan recommends the second: fewer settings, and the UI already
  renders the link conditionally.
- **The OTLP endpoint** in `.env` (`mlflowhpi...`) is not read by the Hermes
  image: `hermes_otel_document` takes its endpoint from `MLFLOW_TRACKING_URI`.
  Setting that internal is enough. `MLFLOW_OTLP_ENDPOINT` is now read by
  nothing at all — it was the OpenHands compose environment, removed in US-010 —
  so it can be deleted from `.env` whenever convenient.

### Rollback

Each step is reversible without touching data:

| Change | Rollback |
| :--- | :--- |
| `ports:` removed from mlflow/rustfs | re-add the lines, `docker compose up -d mlflow rustfs` |
| `RUSTFS_CONSOLE_ENABLE=false` | set `"true"` and restart rustfs |
| Dokploy domain deleted | re-add it in the UI |
| Traefik middleware | detach it from the router |
| `S3_ENDPOINT_URL` / `MLFLOW_TRACKING_URI` internal | restore the public values and restart `backend-api` and `hermes` |

Nothing in this plan deletes the `mlflow_artifacts` or `rustfs_data` volumes,
changes a bucket policy, or rotates a key, so data and credentials survive a
full rollback.

### Verification after applying

1. `docker compose exec backend-api python -c "import urllib.request;
   urllib.request.urlopen('http://mlflow:5000/health')"` succeeds.
2. From outside the host, `curl -sS -o /dev/null -w '%{http_code}'
   https://<old-mlflow-domain>/health` returns `401` (basic-auth) or does not
   resolve.
3. `curl -sS -o /dev/null -w '%{http_code}' https://hpi-s3...` fails to
   resolve.
4. One optimization run completes and the admin screen shows its run, with the
   MLflow link either working through the retained domain or absent.
5. `deepfix eval` writes an MLflow run (it uses the internal URI).

## Not in scope

- Adding auth to the public MLflow server (a Non-Goal in the Hermes PRD). This
  plan proposes the opposite: stop publishing it, or gate it at the ingress.
- Rotating the `hermes-optimizer` key or changing its IAM policy.
- Moving artifacts off the `mlflow_artifacts` volume.
- Any change to the DeepFix API's own auth.

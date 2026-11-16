# Record: MLflow and RustFS lockdown

**Status: applied** in `74b2329`, and pinned by
`tests/test_mlflow_rustfs_lockdown.py`. This file is the record of what was
decided and why. For current behaviour, read `README.md` → "MLflow and RustFS
lockdown" and `docker-compose.yml`, which are the source of truth.

## What was exposed

Before the change, `docker-compose.yml` published a host port for each service
and Dokploy routed a public domain to each. Neither had authentication:

| Service | Published port (before) | Authentication (before) |
| :--- | :--- | :--- |
| MLflow UI and API | `5000` | none |
| RustFS S3 API | `9000` | S3 signature |
| RustFS console | `9001` | none |

Hostnames are deliberately not repeated here. At the time, they lived in
Dokploy's domain settings rather than in this repository: there was no Traefik
label or config file anywhere in the repo, so all routing was Dokploy-side. The
MLflow router and its basic-auth middleware now live in `docker-compose.yml`.

## What was already safe

- Neither service holds the product database.
- The Hermes object-store key is a dedicated `hermes-optimizer` user, scoped by
  IAM to `optimization/runs/*` (`src/deepfix_rag/hermes_iam.py`). It is not the
  root key, and the root key never enters the Hermes container.
- `MLFLOW_SERVER_ALLOWED_HOSTS` is a `Host` allow-list, **not** authentication:
  anyone who sends a permitted `Host` header is served. It reduces noise, it does
  not protect anything.

## What was at risk

1. **MLflow was unauthenticated and writable.** Anyone who found the domain could
   read every experiment, parameter, and eval table, and could delete or alter
   runs. The secret scrub in `optimization._secrets` was the only thing between a
   logged value and a public page.
2. **The RustFS console was exposed on `9001`.** It has no IAM policy in front of
   it, so it is the only full-control surface. It has no consumer in this
   repository.
3. **The S3 API was public.** Signature-protected, so the least urgent, but every
   request from the API and Hermes left the Docker network and came back in
   through a public hostname, which added latency and a credential-stuffing
   surface for no benefit.

## Who actually needs these

Machine-to-machine traffic needs neither service outside the Compose network:
`backend-api`, `hermes`, and `diagnostic-agent` all reach MLflow at
`http://mlflow:5000` and the object store at `http://rustfs:9000`.

Exactly one consumer needs MLflow from outside the network: a human reading the
UI. Nothing needs the RustFS console or a public S3 endpoint.

## What was done

- **No published ports** for `mlflow` or `rustfs`. Both are reachable only by
  Compose service name.
- **`RUSTFS_CONSOLE_ENABLE=false`**, defaulted in `docker-compose.yml` and
  documented in `example.env`. The console is not started.
- **`MLFLOW_TRACKING_URI` and `S3_ENDPOINT_URL` point at the internal names** in
  `example.env`, so no caller depends on a public hostname.
- **One MLflow router exists, declared in `docker-compose.yml`**, behind the
  `deepfix-mlflow-auth` basic-auth middleware that is also declared there. Its
  `Host` comes from `MLFLOW_PUBLIC_HOST` and stays in
  `MLFLOW_SERVER_ALLOWED_HOSTS` with its origin in
  `MLFLOW_SERVER_CORS_ALLOWED_ORIGINS`, because MLflow 3.x rejects an unlisted
  `Host` before the UI loads. The router is version-controlled rather than a
  Dokploy-side edit so a redeploy cannot detach the middleware, and the
  `mlflow` service joins `dokploy-network` itself because Dokploy only attaches
  a service that has a Domain.
- **`mlflow_run_url()` returns nothing for an internal tracking URI**, so the
  admin run table shows a dash instead of a link a browser cannot open, rather
  than gaining a second setting.
- **The RustFS domain was removed** in Dokploy.

`S3_URI`, `S3_REGION`, and the IAM keys are unchanged: the URI names a bucket and
a key, not a host.

## Rejected alternatives

- **Keep MLflow public with no auth.** Not acceptable: it is unauthenticated and
  writable.
- **Forward-auth (Authentik or OIDC).** Real accounts and audit, but a whole
  service to run and keep healthy. More operational surface than one operator
  reading one dashboard justifies.
- **Firewall the ports instead of changing Compose.** Leaves the Dokploy domain
  as a second ingress path and leaves Compose advertising the exposure, so the
  next person reopens it. Usable only as defence in depth.

## Rollback

Every step is reversible and touches no data:

| Change | Rollback |
| :--- | :--- |
| `ports:` removed | re-add the entry and `docker compose up -d <service>` |
| `RUSTFS_CONSOLE_ENABLE=false` | set it to `true` and rebuild `rustfs` |
| Dokploy domain removed | re-add it in the UI |
| Traefik middleware, now a label in `docker-compose.yml` | delete the `deepfix-mlflow-auth` labels and redeploy `mlflow` |
| Internal endpoints | restore the public values and restart `backend-api` and `hermes` |

Nothing here deletes the `mlflow_artifacts` or `rustfs_data` volume, changes a
bucket policy, or rotates a key.

## Verification

`tests/test_mlflow_rustfs_lockdown.py` asserts, against the real Compose files
and `example.env`:

- neither locked service declares `ports:`;
- `RUSTFS_CONSOLE_ENABLE` defaults to `false`;
- every service that reads `MLFLOW_TRACKING_URI` or `S3_ENDPOINT_URL` gets the
  internal value;
- `mlflow_run_url()` returns `None` for an internal tracking URI, and the admin
  screen still shows the run with its MLflow id.

On a deployment, check by hand: `docker compose exec backend-api` can reach
`http://mlflow:5000/health`, the MLflow UI asks for basic-auth credentials, and
an external request to the old S3 hostname does not resolve.

## Not in scope

- Adding authentication to MLflow itself.
- Rotating the `hermes-optimizer` key or changing its IAM policy.
- Moving artifacts off the `mlflow_artifacts` volume.
- DeepFix's own authentication, which is Better Auth in the web app plus the
  identity token FastAPI verifies.

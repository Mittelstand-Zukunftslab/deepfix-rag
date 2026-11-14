# Admin Hermes chat: deployed verification walkthrough

This is the operator procedure for CHAT-012. It runs on the Dokploy deployment,
not in CI and not from a cloud agent. Every command is copy-pasteable from the
deployment host unless it says otherwise.

It verifies the whole feature end to end: both chat states, the two profiles,
memory inside the 4 GB envelope, the four admin journeys, retention with chat
off, the MLflow trace, and the holdout boundary.

## Before you start

- The branch is `feat/hermes-optimizer`. Do not deploy, push, or merge `dev` or
  `main` as part of this.
- `backend-api`, `hermes`, and `web` all need a rebuild: this release changes
  `src/deepfix_rag/**`, `docker/hermes/sandbox_boot.py`, the run-tag plugin, the
  chat page, and both Compose files.
- Set these in the deployment environment first. Key names only; never paste a
  value into a ticket or a log.

| Key | Required when | What it does |
| :--- | :--- | :--- |
| `HERMES_CHAT_API_SERVER_KEY` | chat on | The chat profile's bearer. At least 16 characters, not a placeholder, and distinct from `API_SERVER_KEY`. |
| `HERMES_CHAT_ENABLED` | always | `false` (default) or `true` for the two states below. |
| `HERMES_CHAT_MODEL` | optional | Empty falls back to `HERMES_MODEL`. |
| `HERMES_CHAT_BASE_URL` | optional | Defaults to `http://hermes:8643`. |
| `MLFLOW_HERMES_CHAT_EXPERIMENT` | optional | Defaults to `deepfix_hermes_chat`. |
| `ADMIN_CHAT_AUDIT_RETENTION_DAYS` | optional | Defaults to `14`; `0` keeps audit rows forever. |

The eleven `ADMIN_CHAT_*` limits all have safe defaults in `docker-compose.yml`;
set one only to change it.

## 1. State A: chat off (the default)

Deploy with `HERMES_CHAT_ENABLED=false` and no chat key set.

```bash
docker compose ps
docker port hermes                       # expect: empty
docker exec hermes ps -eo pid,rss,args | grep -i gateway
docker exec hermes ss -ltn               # expect: nothing on 8643
docker stats --no-stream                 # record the hermes row
```

Expected (FR-029, D-1):

- Exactly one gateway process, and its arguments name the optimization profile.
  The pin runs the gateway through a Python interpreter, so match on `gateway`
  plus the profile in the arguments, not on a literal `hermes gateway` name.
- Nothing bound to `8643`.
- The RSS reading is for information only: summed RSS double-counts shared
  pages, so the guarantee is process-and-port absence, not a byte figure.

Then check the route split from `backend-api` (D-4, FR-027):

```bash
# Each of these answers 503 with
# {"detail":{"code":"chat_disabled","message":"Hermes chat is turned off on this server."}}
# POST /admin/chat/completions
# GET  /admin/chat/turns/{turn_id}/stream
# GET  /admin/chat/threads
# POST /admin/chat/threads
# GET  /admin/chat/threads/{thread_id}
# GET  /admin/chat/threads/{thread_id}/runs
# POST /admin/chat/threads/{thread_id}/runs/{run_id}
# POST /admin/chat/actions/{action_id}/confirm
# POST /admin/chat/actions/{action_id}/reject

# The two SQLite-only routes stay reachable.
curl -sS -o /dev/null -w 'audit=%{http_code}\n' -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://backend-api:8000/admin/chat/audit          # expect audit=200
curl -sS -o /dev/null -w 'delete=%{http_code}\n' -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://backend-api:8000/admin/chat/threads/probe  # expect delete=204 (idempotent)
```

On the site, open `/admin/chat` with the network panel open. The disabled page
must render, name `HERMES_CHAT_ENABLED=true`, and make no request to any
`/admin/chat/*` route. The Optimization screen must still start and cancel a run.

## 2. Retention, with chat off (do this in State A)

This is the case that matters most: the prune must run on a deployment that
stopped using chat.

```bash
docker exec backend-api python -c "
from datetime import datetime, timedelta, timezone
from deepfix_rag.storage import connect
from deepfix_rag.config.settings import Settings
db = Settings().product_db_path
now = datetime.now(timezone.utc)
old = (now - timedelta(days=30)).isoformat()
fresh = (now - timedelta(days=1)).isoformat()
with connect(db) as c:
    for label, at in (('old', old), ('fresh', fresh)):
        c.execute(
            'INSERT INTO admin_chat_audit (at, admin_id, admin_email, thread_id, turn_id, kind, detail)'
            ' VALUES (?,?,?,?,?,?,?)',
            (at, 'probe-' + label, 'probe@example.com', None, None, 'turn_started', 'retention probe'),
        )
print('inserted', old, fresh)
"
```

Wait for the next hourly tick. The job also fires once when the API starts, so
restarting `backend-api` is the fastest way to see it:

```bash
docker logs --tail 200 backend-api | grep 'chat audit prune'
docker exec backend-api python -c "
from deepfix_rag.storage import connect
from deepfix_rag.config.settings import Settings
with connect(Settings().product_db_path) as c:
    rows = dict(c.execute(
        \"SELECT admin_id, COUNT(*) FROM admin_chat_audit WHERE admin_id LIKE 'probe-%' GROUP BY admin_id\").fetchall())
print(rows)
"
```

Expected: one `chat audit prune deleted=<n> retention_days=14` line, no
`probe-old` row, and `probe-fresh` still present and still returned by
`GET /admin/chat/audit`. With `ADMIN_CHAT_AUDIT_RETENTION_DAYS=0` nothing is
deleted and no line is written.

Clean up the probe rows when you are done:

```bash
docker exec backend-api python -c "
from deepfix_rag.storage import connect
from deepfix_rag.config.settings import Settings
with connect(Settings().product_db_path) as c:
    c.execute(\"DELETE FROM admin_chat_audit WHERE admin_id LIKE 'probe-%'\")
"
```

## 3. State B: chat on

Set `HERMES_CHAT_ENABLED=true` and `HERMES_CHAT_API_SERVER_KEY`, redeploy, and
repeat the listing:

```bash
docker port hermes                       # still empty: nothing is published
docker exec hermes ps -eo pid,rss,args | grep -i gateway   # two processes now
docker exec hermes ss -ltn               # 8643 is bound
docker stats --no-stream                 # record both rows
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8643/health || echo "refused (expected)"
```

The health check from the API container, then the standalone proof (D-1, AC 2):

```bash
docker exec backend-api python -c "import urllib.request;print(urllib.request.urlopen('http://hermes:8643/health').read())"

# Expect a chat.completion. Use the real id from HERMES_CHAT_MODEL, else HERMES_MODEL.
docker exec backend-api python -c "
import json, os, urllib.request
body = json.dumps({'model': os.environ.get('HERMES_CHAT_MODEL') or os.environ['HERMES_MODEL'],
                  'messages': [{'role': 'user', 'content': 'say ok'}]}).encode()
req = urllib.request.Request(
    'http://hermes:8643/v1/chat/completions', data=body,
    headers={'Authorization': 'Bearer ' + os.environ['HERMES_CHAT_API_SERVER_KEY'],
             'Content-Type': 'application/json'})
print(urllib.request.urlopen(req).status)
"

# The negative check: the optimization gateway must NOT serve /p/chat/.
docker exec backend-api python -c "
import urllib.request
try:
    urllib.request.urlopen('http://hermes:8642/p/chat/v1/chat/completions', timeout=5)
    print('UNEXPECTED: the optimization gateway served /p/chat/')
except Exception as exc:
    print('refused as expected:', type(exc).__name__)
"
```

If `/p/chat/` answers, the pin is multiplexing. D-1's remedy is a second
`hermes` service with a chat-only home; that is not a fix to make in place.

## 4. The four journeys (chat on)

On the deployed site, signed in as an admin, with the browser network panel open.

J1, a first answer. Ask about this server ("what is the current schedule and
champion?"). Expect a streamed, markdown-rendered answer that reflects the real
schedule, champion diff, and recent runs. No frame in the network panel may
contain a Hermes key, an OpenRouter key, an S3 secret, a private path, or an
upstream model id.

J2, an action card. Ask the assistant to start a run, or wait until it proposes
one, then confirm the card. Expect a run row and a run panel that moves
`running` to a terminal status with the reason, the deltas, the MLflow link, and
the step tail. No holdout question text may appear anywhere.

J3, stop and retry. Send a message and press Stop mid-reply. The partial text
must stay visible and be stored, and a linked run must be unchanged. Then retry:
a new turn and a new reply.

J4, delete. Delete a thread that started a run. The conversation goes, the run
row and its step log stay, and `GET /admin/chat/audit` still returns the rows
with `thread_deleted: true` and no message text.

Then reload mid-reply: close the tab during a reply and reopen the thread. The
reply resumes with no duplicated text.

## 5. The trace

Open the chat turn in MLflow. Expect the experiment `deepfix_hermes_chat` (or
your `MLFLOW_HERMES_CHAT_EXPERIMENT`), spans carrying `deepfix.chat_thread_id`
and `deepfix.chat_turn_id`, and no prompt or reply text, because
`content_capture` is `off`. The optimizer's rounds must still be in
`deepfix_hermes`.

## 6. The holdout boundary

Take one known holdout phrase and search for it in every place a chat turn can
write: a chat reply, an `/admin/chat/audit` row, a step tail, and an MLflow
trace. It must be absent from all four.

## 7. Memory under load

With chat on and a run in flight, read `docker stats --no-stream`. If the 4 GB
limit is approached, the cap to lower is `ADMIN_CHAT_MAX_CONCURRENT_TURNS`
(2 by default), then `ADMIN_CHAT_TURN_TIMEOUT_SECONDS` or
`ADMIN_CHAT_MAX_PROMPT_CHARS`. Do not raise `mem_limit`, `cpus`,
`agent.max_turns`, or `OPTIMIZATION_RUN_TIMEOUT_HOURS`.

## 8. Thread-load timing

Time a thread load on the pilot data from the browser network panel and record
the figure. The PRD's 300 ms target is recorded, not asserted.

## Recording the result

Append a note to
`tasks/stories-admin-hermes-chat/CHAT-012-deployed-verification.md` with the date
and deploy id, both memory readings, both process and port listings, both
retention outcomes, the four journeys and the reload, the trace inspection, the
holdout search, and any step that failed. A defect found here becomes its own fix
commit on the branch, not an edit inside this story.

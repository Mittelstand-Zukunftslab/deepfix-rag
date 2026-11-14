# CHAT-012 — Deployed verification on Dokploy

## Type
Chore

## Status
Draft

## Summary
One end-to-end pass on the deployed stack proves the feature works outside this VM: both chat states behave as
decided, memory stays inside the envelope, and an admin completes all four journeys on the real site. This is the
story that turns the PRD's unmeasured assumptions into measurements.

## PRD traceability
- Related: all FRs, verified end to end; FR-029 in particular (the process-and-port check), FR-024 (the trace
  exists), FR-027 (the disabled page makes no request), FR-030 (the prune runs with chat off)
- Decisions: D-1, D-5 and D-6 are the three this story measures; D-2, D-3 and D-4 are the three it observes in the
  UI
- Journeys: J1, J2, J3, J4
- NFR touchpoints: NFR-2 (memory), NFR-3 (the 300 ms thread-load measurement, recorded not asserted), NFR-5
  (no holdout content anywhere)

## Dependencies
- Requires: CHAT-001 through CHAT-011 landed on `feat/hermes-optimizer` and deployed to Dokploy.
- Blocks: nothing. It is the last story.

## Acceptance criteria

Scope:

1. Given the story, when it is executed, then it is run by the operator on the Dokploy deployment, not by a cloud
   agent, and it pushes nothing to `dev` or `main`.

Both states, both profiles:

2. Given `HERMES_CHAT_ENABLED=true`, when the deployment is inspected, then `docker port hermes` is empty, `curl`
   from the host to 8642 and 8643 is refused, `GET http://hermes:8643/health` succeeds from `backend-api`, and
   `POST http://hermes:8642/p/chat/v1/chat/completions` fails.
3. Given `HERMES_CHAT_ENABLED=false`, when the deployment is inspected, then nothing listens on 8643, the nine
   Hermes-facing chat routes return 503 with `code: "chat_disabled"`, `GET /admin/chat/audit` returns 200, and
   `DELETE /admin/chat/threads/{id}` returns 204.
4. Given chat disabled, when `ps -eo pid,rss,args` runs inside the container, then exactly one `hermes gateway`
   process is listed (matched on arguments and profile), and `ss -ltn` shows nothing on 8643.
5. Given chat enabled, when the same two commands run, then a second gateway process exists for the `chat` profile
   and 8643 is bound. `docker stats --no-stream` is recorded in both states for information only.
6. Given chat on during a run, when the 4 GB limit is approached, then the report names which cap to lower
   (`ADMIN_CHAT_MAX_CONCURRENT_TURNS` first) and no cap is raised.

Journeys:

7. Given an admin on the deployed site, when they complete J1, then a streamed answer arrives and renders
   markdown, and the network panel shows no key, no S3 secret, and no model id in any frame.
8. Given J2, when the admin confirms an action card, then a run starts, the run panel moves from `running` to a
   terminal status with the reason, the deltas, the MLflow link, and the step tail, and no holdout rows appear.
9. Given J3, when the admin presses Stop, then the partial text stays visible and is stored, the run is not
   cancelled, and a retry produces a new reply.
10. Given J4, when the admin deletes a thread, then the conversation disappears, the run it started is intact, and
    the audit view shows the surviving rows with `thread_deleted: true` and no message text.
11. Given the retention prune on the deployed stack, when an audit row older than `ADMIN_CHAT_AUDIT_RETENTION_DAYS`
    exists, then the row is gone after the next scheduler tick and the API log carries one
    `chat audit prune deleted=<n> retention_days=<d>` line. A row inside the window is still returned by
    `GET /admin/chat/audit`. Checked by inserting a row with an old `at` through the API container's SQLite file,
    then waiting for the tick; also checked once with `HERMES_CHAT_ENABLED=false`, which is the case that matters
    most because the audit route stays reachable then.
12. Given a reload during a reply, when the page reopens, then the reply resumes with no duplicated text.
13. Given `HERMES_CHAT_ENABLED=false` on the deployed site, when the admin opens `/admin/chat`, then the disabled
    page renders, the network panel shows no chat request, and the Optimization screen still starts and cancels a
    run.
14. Given an MLflow trace for a chat turn, when it is opened, then it is in `deepfix_hermes_chat`, carries
    `deepfix.chat_thread_id` and `deepfix.chat_turn_id`, and carries no prompt or reply text.
15. Given a known holdout, when it is searched for in any chat response, audit row, step tail, or trace, then it is
    absent.
16. Given a thread load, when it is timed on the pilot data, then the measurement is recorded (the PRD's 300 ms
    target is recorded here, not asserted in a test).

Report:

17. Given the pass, when it finishes, then the results are appended to this file's verification note with the exact
    commands, the two memory readings, the process and port evidence, the retention check's two states, and the
    journeys' outcomes, including any step that failed.

## Test commands

Run on the deployment host (the operator's environment, not this VM):

```bash
docker compose ps
docker exec hermes ps -eo pid,rss,args
docker exec hermes ss -ltn
docker stats --no-stream
docker port hermes
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8643/health || echo "refused (expected from host)"
docker exec backend-api python -c "import urllib.request;print(urllib.request.urlopen('http://hermes:8643/health').read())"
curl -sS -X POST http://hermes:8642/p/chat/v1/chat/completions || echo "refused (expected: standalone)"
# Retention: insert an audit row past the window, then look for it after the next tick.
docker exec backend-api python -c "
from datetime import datetime, timedelta, timezone
from deepfix_rag.storage import connect
from deepfix_rag.config.settings import Settings
db = Settings().product_db_path
old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
with connect(db) as c:
    c.execute('INSERT INTO admin_chat_audit (at, admin_id, admin_email, thread_id, turn_id, kind, detail) VALUES (?,?,?,?,?,?,?)',
              (old, 'probe', 'probe@example.com', None, None, 'turn_started', 'retention probe'))
print('inserted', old)
"
docker logs --tail 200 backend-api | grep 'chat audit prune' || echo "no prune line yet (wait for the hourly tick)"
docker exec backend-api python -c "
from deepfix_rag.storage import connect
from deepfix_rag.config.settings import Settings
with connect(Settings().product_db_path) as c:
    print(c.execute(\"SELECT COUNT(*) FROM admin_chat_audit WHERE admin_id='probe'\").fetchone()[0])
"
```

Browser: the four journeys on the deployed admin account, with the network panel open; then repeat with
`HERMES_CHAT_ENABLED=false` after redeploying with the flag off.

## Out of scope
- Any code change. If the pass finds a defect, it becomes a new story or a fix commit on its own branch, not an
  edit inside this one.
- Raising any resource cap.
- Pushing to `dev` or `main`.
- Load or soak testing beyond the single run-plus-chat memory reading.

## Notes
- This is the only story a cloud agent cannot execute. It stays `Draft` until the operator runs it; the code
  stories are done when they are committed and green, and the PRD's release acceptance criterion 9 (the two
  misconfiguration messages), criterion 8 (chat off behaves), and criterion 11 (the prune runs with chat off) are
  settled here rather than by inference.
- AC 2's negative check on 8642 is the standalone proof: if `/p/chat/v1/chat/completions` answers, the pin is
  multiplexing and D-1's fallback (a second `hermes` service with a chat-only home) is the remedy.
- Record the reading before and after a run so "chat plus a run stays under 4 GB" is evidence, not a hope.
- The trace check needs an MLflow link the admin screen already builds; do not add a second URL builder for it.

## Definition of done
- [ ] Executed on the deployment by the operator
- [ ] Verification note appended with commands and outcomes for every AC
- [ ] Any defect filed as its own story or fix commit
- [ ] No push to `dev` or `main`

## Verification note
Fill in on execution: date, deploy id, both memory readings, process and port listings in both states, the two
retention checks (row deleted, row inside the window kept, both with chat off), the four journey outcomes, the
trace inspection, and the holdout search result.

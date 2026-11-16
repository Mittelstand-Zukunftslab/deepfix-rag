# Verifying the Ask chat on a deployed stack

This is the runbook for ASKCHAT-011. It is an **operator session**, not an automated test: it proves the things a
local suite cannot — that the BFF streams a body unchanged, that the pinned provider model honours
`tool_choice`, that four concurrent turns leave the host where it was, and that the surfaces shipped before this
feature still work.

## Status: prepared, not run

**Not run.** This document was written by an agent that had no deployment, no operator session, and no browser against
a live stack. Every step below is written out with the command or action and the exact observation to record, and
every result field reads `NOT RUN`. An operator executes the steps, replaces each `NOT RUN` with the observed value,
and signs the header. A step with no recorded output is not done — that is the story's own rule (AC 2).

| Field | Value |
| :--- | :--- |
| Deployment | NOT RUN |
| Date of session | NOT RUN |
| Operator | NOT RUN |
| Commit tested | NOT RUN |
| API image digest | NOT RUN |
| Web image digest | NOT RUN |
| `GET /health` payload | NOT RUN |
| Branch and commit the deployment was built from | expected `feat/hermes-optimizer`; NOT RUN |

Record the commit the images were **built from**, not the branch tip: a rebuild after the push is what makes them
differ, and a runbook that names a branch is a claim about intent rather than about the artefact.

## Before starting

Two accounts are needed, which is why AC 7 can be checked at all:

- **Member A**, who asks the questions and casts the votes. Every "the member" below is A.
- **Member B**, a second member with no conversations of their own, used only to prove a 404.

And one piece of prior state: a corpus with documents indexed, and a champion config with `rerank.enabled: true` and
`min_relevance_score: 0.3`. The four badge states (step 8) need real rerank scores, so they cannot be produced on a
deployment with rerank off.

Record the baseline before touching the chat:

```bash
docker compose ps --format '{{.Service}}\t{{.Status}}'
docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
docker inspect -f '{{.Name}} mem={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}}' \
  $(docker compose ps -q)
```

The expected caps, which must not change during this session: `hermes` 4 GB with `cpus: "2"`, `mlflow` 2 GB,
`rustfs` 1 GB, `diagnostic-agent` 1 GB, and `backend-api` and `web` uncapped. Any other value is a finding for
step 9.

## 1. The stream, on the wire (AC 3)

Open `/chat` in a browser with the network panel recording, ask one question that the corpus answers, and inspect the
`POST /api/ask/chat/completions` request.

Record:

| Observation | Expected | Result |
| :--- | :--- | :--- |
| Response `Content-Type` | `text/event-stream` | NOT RUN |
| Response header `x-vercel-ai-ui-message-stream` | `v1` | NOT RUN |
| Time to first `text-delta` frame, and time to `data: [DONE]` | first is much sooner; frames arrive in more than one burst | NOT RUN |
| The frame types seen, in order | `start`, `start-step`, `text-*`, `tool-input-*`, `tool-output-*`, `data-deepfix-grounding`, `data-deepfix-turn`, `finish`, `[DONE]` | NOT RUN |
| Model id in any frame | none | NOT RUN |
| Any API key or S3 secret in any frame | none | NOT RUN |

For the last two, copy the whole response body into a file and search it:

```bash
grep -Eio 'qwen/|gpt-|openrouter|sk-[a-z0-9-]{8,}|AKIA[0-9A-Z]{16}' /tmp/ask-stream.txt
```

No output is the pass. The design point worth recording: `src/deepfix_rag/api/chat_stream.py` builds every part rather
than forwarding an upstream object, so there is no `model` field to remove — the check confirms the property on the
wire rather than trusting it.

## 2. Stop (AC 4)

Ask a question long enough to answer for a few seconds, then press **Stop** while the text is arriving.

```bash
# on the host, with the deployment's database volume mounted or reachable
sqlite3 /data/product/product.db \
  "SELECT status, ended_at IS NOT NULL AS ended, length(error) FROM ask_chat_turns ORDER BY started_at DESC LIMIT 1;"
```

| Observation | Expected | Result |
| :--- | :--- | :--- |
| Connection closed when Stop was pressed | yes, and the composer became usable again | NOT RUN |
| `POST /ask/chat/turns/{turn_id}/cancel` was sent | yes, 204 — Stop is an explicit request, not just a closed socket | NOT RUN |
| Database row | `cancelled`, `ended=1` | NOT RUN |
| `error` column | `Stopped by the user.` — not a sentence about the browser | NOT RUN |
| Partial text still on screen | yes | NOT RUN |
| Sources panel still shows the passages it had retrieved | yes | NOT RUN |
| Badge | **Stopped, partial answer**, or **Stopped before answering** if no word arrived — never **Partly grounded** | NOT RUN |
| Badge appears without a reload | yes — the cancelled stream closes its text group and sends the verdict, and the badge reads the Stop immediately rather than waiting for the store | NOT RUN |
| The same badge after a reload | unchanged, and present even when the turn stopped before its first word (no verdict stored) | NOT RUN |
| `first_token_ms` on the row | not NULL when any text arrived | NOT RUN |
| Any traceback in `docker compose logs backend-api` | none — a cancel is a normal end | NOT RUN |

A cancelled turn that loses its text is worse than a failed one, because the member cannot tell what they had; if the
partial text is gone, that is a defect, not a finding. If the badge reads **Partly grounded**, the stored turn has a
`grounded` verdict with no citations, which a stopped turn must no longer produce.

**Stop is not the same as leaving the page.** A closed socket used to cancel the turn, which meant a reload mid-answer
lost the reply; section 4 covers that, and it no longer stops the work.

## 2a. How long the first word takes, and whether it searched (AC 6b)

This is the check that found the 93-second silence, and the one that found the model refusing before it looked. Ask a
question the corpus answers — **a long one**, since the short ones passed even when the long ones failed — with the
browser's network panel open, and record when the first *visible character* arrives; not when the response starts.

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The first action | a `search_documents` call — no prose before it, no self-introduction, no refusal | NOT RUN |
| Time to first visible character | a few seconds, not tens of seconds | NOT RUN |
| **Thinking…** shown while the model is silent | yes, and it disappears when the first word arrives | NOT RUN |
| Any silent gap longer than 15 s on the wire | none — `: keepalive` comments fill it | NOT RUN |
| Time to the finished answer | record it | NOT RUN |
| An answer that searched and cited | graded **Grounded**, never "Not found in the documents" | NOT RUN |

Run it several times: the failure this catches was intermittent — 4 of 7 turns — and a single passing turn says
nothing.

```bash
# What the turn actually recorded, and how each step was allowed to think.
docker compose exec backend-api sh -c 'env | grep -E "ASK_CHAT_(REASONING|TOOL_STEP_REASONING|MAX_OUTPUT_TOKENS|TURN_TIMEOUT)"'
sqlite3 /data/product/product.db \
  "SELECT turn_id, status, grounding, first_token_ms, length(retrieval_query) AS searched, error \
   FROM ask_chat_turns ORDER BY started_at DESC LIMIT 5;"
```

`ASK_CHAT_REASONING=off` (the answer step) and `ASK_CHAT_TOOL_STEP_REASONING=low` (a step that may call the tool) are
the defaults, with `ASK_CHAT_MAX_OUTPUT_TOKENS=2048`. A turn with `first_token_ms` far above the searches' duration, or
`grounding` reading `declined` on an answer with citations, is the defect those settings exist to prevent. A gap over
about 100 s would also be dropped by Cloudflare, so a long silence is both a latency defect and a truncated answer
waiting to happen (D-12).

If one step's hidden reasoning consumes the whole output budget, raise `ASK_CHAT_MAX_OUTPUT_TOKENS` — reasoning tokens
count against it — before considering a higher reasoning level, which delays the first word instead.

## 3. A follow-up, and what the export asks (AC 5)

Ask a first question, then a follow-up that refers back to it — "and who decides that?" rather than a restated
question — and let the model search.

```bash
sqlite3 /data/product/product.db \
  "SELECT seq, query, retrieval_query FROM ask_chat_turns t \
   JOIN interactions i ON i.turn_id = t.turn_id ORDER BY seq;"
```

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The tool call's query on the follow-up | standalone, not a pronoun sentence | NOT RUN |
| `ask_chat_turns.retrieval_query` on the follow-up | that standalone query | NOT RUN |
| `interactions.query` on the follow-up | the member's literal message, unchanged | NOT RUN |
| The exported dev question for that turn (`deepfix feedback export`, or MLflow's feedback run) | the standalone query | NOT RUN |

This is FR-021 and it is the difference between a usable dev question and one the optimizer would score as a
retrieval miss.

## 4. Reload mid-answer (AC 6)
Ask a question, and reload the page while the answer is still streaming.

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The answer on reload | continues — the page resumes the running turn and renders the rest of it | NOT RUN |
| A duplicated paragraph or sentence | none | NOT RUN |
| The turn's status after the reload finishes | `completed`, with the whole answer — the reload does **not** cancel it | NOT RUN |
| The badge | the verdict that turn reached: a turn that searched reads **Grounded**, not "Reused earlier evidence" | NOT RUN |
| No badge inherited from the previous answer | yes — a turn with no verdict yet shows no badge rather than the older turn's | NOT RUN |

Record whether the deployment runs more than one uvicorn worker:

```bash
docker compose exec backend-api sh -c 'ps aux | grep -c "[u]vicorn"'
```

One worker is assumed throughout. A resume on a second worker degrades to a stored-text replay, which is a correct
answer rather than a bug, but the operator should know which behaviour they saw.

## 5. Privacy (AC 7)

| Observation | Expected | Result |
| :--- | :--- | :--- |
| Member A's thread list in a second browser | the same conversations | NOT RUN |
| Member B requesting A's thread id, by `curl` with B's own token | `404`, not `403` | NOT RUN |
| Member B's conversation list | empty | NOT RUN |

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $MEMBER_B_TOKEN" \
  "https://<host>/api/ask/chat/threads/<member-a-thread-id>"
```

## 6. The four badge states (AC 8)

This is the part that is easy to fake locally and hard to see on a deployment, because the states depend on real
rerank scores. Ask four questions and record which one produced each state.

| State | How to provoke it | Question used | Result |
| :--- | :--- | :--- | :--- |
| **Grounded** (with citations) | a question the corpus answers, where the model cites a passage | NOT RUN | NOT RUN |
| **Partly grounded** (no citations) | a question the corpus answers where the model answers without a `[n]` marker — often a short factual question | NOT RUN | NOT RUN |
| **Not found in the documents** — "Answer declined" | a question the passages do not cover, where the model refuses in prose | NOT RUN | NOT RUN |
| **Not found in the documents** — "No matching passages" | a question nothing in the corpus is close to, so the floor drops every candidate | NOT RUN | NOT RUN |

Record the grounding value the API reported for each, so the rendered state can be tied back to the stored one:

```bash
sqlite3 /data/product/product.db \
  "SELECT turn_id, grounding, citations FROM ask_chat_turns ORDER BY started_at DESC LIMIT 4;"
```

If a state cannot be produced, record which and why rather than leaving the row blank: a state that never renders on a
real deployment is a product finding.

## 6a. The layout at a narrow width, and the sources panel's own box

The Ask page was reported broken at about 630px effective width — at 200% zoom — and the sources panel scrolled
sideways at a normal desktop width. Neither is visible in a jsdom test, so check it in a browser.

Set the window to about 630px wide (or 200% zoom on a 1260px window) with a thread that has passages, and check:

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The sources panel | stacks **below** the conversation, full width, with a top border | NOT RUN |
| The question bubble and the composer | fully visible, not covered by the panel | NOT RUN |
| Scrolling the conversation | the composer stays reachable at the bottom | NOT RUN |
| Panel header | `N passages` is not clipped | NOT RUN |

Then the smallest case, which a first pass at this missed: **250% zoom on a 1280×800 window** — about 512×320 CSS
pixels. This is where the chat column collapsed to roughly 48 px, the composer's hint was clipped at the bottom edge,
and the stacked sources could not be scrolled to at all.

The scroll container is `AppShell`'s `<main>`, not the page and not the window: the shell is `h-screen
overflow-hidden`, so anything the page clips itself can never be scrolled by anything. Check that the **page scrolls**
and that the sources arrive, rather than that a window scrollbar moves.

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The chat column | keeps a usable height — the conversation and the composer both legible, not collapsed | NOT RUN |
| The footer hint and the Regenerate link | not overlapping each other or the **Sources** heading, and not clipped at the bottom edge | NOT RUN |
| Scrolling to the bottom | the **Sources** panel and its heading come into view; the page scrolls rather than the chat being squeezed | NOT RUN |
| A horizontal scrollbar anywhere | none | NOT RUN |
| Inside the sources panel | only its passage list scrolls; the **Sources** heading stays visible | NOT RUN |

Then widen to a normal desktop width:

| Observation | Expected | Result |
| :--- | :--- | :--- |
| The layout | side by side, not stacked | NOT RUN |
| The page | does **not** scroll; the columns fill the viewport and scroll internally | NOT RUN |
| A horizontal scrollbar in the sources panel | none | NOT RUN |
| Each card's score (`0.xxx`) | fully visible at the trailing edge | NOT RUN |
| A long filename | ellipsised, with the full name in its `title` | NOT RUN |
| The conversation column | keeps a usable width beside the panel | NOT RUN |

## 7. Resources under four concurrent turns (AC 9)

Open four members' tabs — or four questions from A, if the deployment has one member — and send a turn in each within
a few seconds, then sample while they run:

```bash
for i in 1 2 3 4 5 6; do
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' \
    $(docker compose ps -q backend-api)
  sleep 5
done
```

| Observation | Expected | Result |
| :--- | :--- | :--- |
| New container during the test | none | NOT RUN |
| `mem_limit` / `cpus` on any service after the test | unchanged from the baseline | NOT RUN |
| `backend-api` memory at peak | record the number | NOT RUN |
| `backend-api` memory ten minutes after | back to its pre-test level | NOT RUN |
| `ASK_CHAT_MAX_CONCURRENT_TURNS` in the running container | `4` | NOT RUN |

```bash
docker compose exec backend-api printenv | grep '^ASK_CHAT_' | sort
```

A fifth concurrent turn should answer `429` with the "busy" sentence rather than the member's own budget. Record what
it said.

## 8. Does the model search when it should (AC 10)

This is where D-4b's rule is checked on the real stack, because the decision now
lives in the system prompt and only a live model can show whether it obeys. Ask both
kinds of turn and read each one's trace in the `deepfix_product` experiment:

| Observation | Expected | Result |
| :--- | :--- | :--- |
| A real question: did the model call `search_documents`? | `yes` | NOT RUN |
| A real question: was the badge **Grounded** with at least one citation? | `yes` | NOT RUN |
| "Thanks": did the turn make any tool call? | `no` | NOT RUN |
| "Thanks": was the badge **Not searched** with "Answered without searching"? | `yes` | NOT RUN |
| "Make that shorter": did the turn answer without a new search, and render **Grounded**? | `yes` | NOT RUN |
| Every step's `tool_choice` on the wire | `auto`, with `none` only on the last permitted step | NOT RUN |
| Trace name and tags | `ask_chat`, tagged `interaction_id`, `thread_id`, `turn_id`, `grounding`, `status` | NOT RUN |
| Child spans for a turn with one search | one `search_documents` span carrying `query`, `passage_count`, `max_score`, `duration_ms` | NOT RUN |

A "thanks" that searches is a **finding**: the prompt is not steering the model,
and the fix is the prompt or a different `LLM_MODEL`, not a return to forcing the
tool. A real question that answers as **Not searched** is a worse finding, because
it means an answer about the corpus came from the model's own knowledge; record the
question and the model id with it.

## 9. What a stopped turn cost (AC 11)

Compare the provider's usage for a stopped turn with a completed one of a similar question, in the OpenRouter
dashboard or the provider's usage log for the same window.

| Observation | Result |
| :--- | :--- |
| Tokens billed for the completed turn | NOT RUN |
| Tokens billed for the stopped turn | NOT RUN |
| Did the stopped turn's spend stop with it? | NOT RUN |

This is the residual cost R-1 and R-2 describe. If the stopped turn bills the full completion, the generator close in
`ask_chat_turn._stream_completion` is not reaching the provider, which is a defect worth its own story.

**A reload now costs what the answer costs.** The turn outlives the request, so closing the tab no longer stops the
spend — that is the trade that keeps the reply (FR-016). The turn's own `ASK_CHAT_TURN_TIMEOUT_SECONDS` bounds it.
Record one case while you are here: ask a question, close the tab immediately, and confirm in the usage log that the
turn ran to its natural end rather than to the timeout, so the bound is real but rarely reached.

## 10. The shipped surfaces still work (AC 12)

| Surface | Action | Expected | Result |
| :--- | :--- | :--- | :--- |
| Search (`/search`) | search a term | ranked passages with votes, as before | NOT RUN |
| Single-turn `POST /ask` | ask a question | one answer with passages and a grounding badge | NOT RUN |
| Admin Hermes chat (`/admin/chat`) | send a message | replies stream, action cards render if proposed | NOT RUN |
| Optimization (`/admin`) | read the schedule and champion | renders, `next_run_at` present | NOT RUN |
| History (`/history`) | open one ask | the review screen loads with its votes | NOT RUN |

The History page is `/history`; `GET /admin/interactions` is its **API**, not a page.
Typing the API path into the address bar is a 404 by design — there is no browser
route there, and this runbook named the wrong one before the deployed check found
it.

This is FR-022 and it is verified **here and nowhere else**: every other story's definition of done runs the
unchanged suites, which cannot prove the deployed surfaces still work.

## What to do with the result

Write the observed values into this document, replace every `NOT RUN`, and commit it on the branch the deployment was
built from. If a step found a defect, the fix is its own story — this one makes no code change (the story's own
definition of done says so).

Anything in this session that could become an automated test is named as a follow-up rather than claimed as covered.
Candidates worth naming, if the operator finds them worth automating:

- The stream's header and frame order, over a real socket — partly covered by `tests/test_ask_chat_stream.py` and the
  AI SDK chunk-schema check in `web/src/test/ask-chat-stream-contract.test.ts`, but not through the deployed proxy.
- The 404 for another member's thread id — covered locally by `tests/test_ask_chat_threads.py`; the deployment proves
  the proxy forwards the bearer token unchanged.
- The four badge states as rendered — covered by `web/src/test/ask-chat-sources.test.tsx` with fixed citations; only a
  deployment proves them against real rerank scores.

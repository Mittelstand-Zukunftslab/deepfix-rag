# PRD: Ask becomes a streaming, multi-turn chat with the RAG as a tool

> **Status: built. ASKCHAT-001 … ASKCHAT-010 are implemented and committed on
> `feat/ask-chat-streaming`; ASKCHAT-011 is a prepared operator runbook that has
> not been run.** Drafted against the tree at `642f720`, re-reviewed at `223a122`
> on `feat/hermes-optimizer`, 15 November 2026, after ASK-001, ASK-002 and
> ASK-006 landed. All decisions are settled: **D-1 … D-6 and D-8** while the
> document was written, **D-7 and D-9 … D-11** by the product owner on
> 2026-11-15.
>
> **This revision is the post-implementation correction pass.** It describes what
> was actually built rather than what was planned, and it fixes the fifteen places
> the stories turned out to be wrong plus two behaviours inherited from ASKCHAT-001
> (see "Corrections after implementation" below). Every correction is a change to
> this document and to `tasks/stories-ask-chat/`; **no production code changed in
> this pass**.
>
> Two behaviours are documented as current and **left as open decisions** rather
> than quietly fixed: a `DELETE` of an already-deleted thread answers 404 (not an
> idempotent 204), and `rename_thread` trims before it measures the 120-character
> bound, so the route's `max_length` and the store disagree at the edge. See
> "Open questions".
>
> Validated with `validate-prd` — see
> `tasks/prd-ask-streaming-multiturn-validation.md` and "Validation
> (validate-prd)" below. The stories are in `tasks/stories-ask-chat/`.

## Overview

The Ask page (`web/src/features/chat/chat-page.tsx`, 989 lines) is a chat-shaped
form: it renders a conversation, but it is not a conversation. One question goes
out as a single non-streaming `POST /api/ask`, the whole page waits behind one
`pending` boolean, there is no way to interrupt an answer, and the previous
questions are not sent anywhere — `POST /ask` is single-turn by design and says
so in its own docstring ("Earlier questions are not included."). Sessions live
only in `window.localStorage`, so a member's history exists on exactly one
browser. Follow-ups like "and what did the ministry say about that?" retrieve on
their own words and usually miss.

Meanwhile the admin Hermes chat on the same branch already does what the Ask
page needs: FastAPI streams the Vercel AI SDK **UI message stream**
(`x-vercel-ai-ui-message-stream: v1`) from `POST /admin/chat/completions`,
Assistant UI runs on the AI SDK through `@assistant-ui/react-ai-sdk`, threads
are server-side, Stop writes `cancelled` and keeps the partial text, and
regenerate replaces an answer in place instead of appending a second one
(CHAT-001 … CHAT-023).

This feature turns Ask into a real chat on that machinery, and turns the DeepFix
RAG into a **tool** the model calls. The Vercel AI SDK client on the front end
receives real tool-call and tool-result parts, renders the retrieved passages
from them, and shows a grounding verdict that comes from the passages the answer
actually used.

**Target users.** Members of the shared workspace (any signed-in `member` or
`admin`) asking questions of the shared corpus.

**Success definition.** A member asks a follow-up question and gets an answer
that used the earlier turn's context; the answer's words appear progressively;
Stop ends an answer within a second and keeps what had been written; reloading
the page shows the same conversation from the server, on any browser; and the
passages behind an answer are on screen, each still votable, with the vote still
reaching the same MLflow trace. The optimizer keeps scoring exactly what the
single-turn pipeline scores, and `apply_candidate` remains the only writer of
the champion.

### Clarifying questions, answered from the brief

The `create-prd` skill asks three to five questions before drafting. The brief
answers them, so no question blocks the draft; each answer is recorded here
because the stories depend on it.

1. **What is the primary goal?**
   A. Streaming answers with a working Stop, real multi-turn conversation, and
   the RAG exposed as a tool to an AI SDK client.
   B. A cosmetic restyle of the existing page.
   C. A separate chat product beside Ask.
   Answer: **A.** Verbatim from the goal.

2. **Where does the agent loop run?**
   A. deepfix-api, streaming the AI SDK UI message stream, as the admin chat
   does.
   B. A TypeScript route in the web server using AI SDK `streamText` with a tool
   that calls the backend retrieval route.
   Answer: **A**, with B recorded as the rejected alternative (D-1).

3. **Does the model have to search before it answers?**
   A. Yes. A turn always retrieves, and the server guarantees it even if the
   model's first step produces no tool call.
   B. No. The model decides, and may answer from its own knowledge.
   Answer: **A** (D-2). A RAG product whose answers may skip the corpus cannot
   report a grounding status honestly.

4. **What happens to today's localStorage sessions?**
   A. Imported once into server-side threads, best effort, then removed.
   B. Dropped with a one-time notice.
   Answer: **A** is the recommendation (D-5). B is cheaper and is the fallback if
   the import story (ASKCHAT-009) is cut.

5. **May this feature touch the evaluation path?**
   A. No. `deepfix eval`, the holdout split, the promotion gate, and
   `apply_candidate` stay byte-for-byte as they are; the chat is a consumer of
   the same champion config and the same index.
   B. Yes. Multi-turn conversation becomes a new eval surface.
   Answer: **A** (D-6). B is a follow-up project, not this release.

## Decisions

Each decision records the alternatives it replaces, so a reader can see what was
considered and why it lost.

**D-1 … D-6 and D-8 were settled while this document was written. D-7 and the
four questions that were open on the first draft were answered by the product
owner on 2026-11-15.** D-7 is rewritten around its answer, and D-9, D-10, and
D-11 record the other three, each stating what it replaces so a reader can find
the earlier text it supersedes.

### D-1: The agent loop runs in deepfix-api, not in the web server

```
Browser (Assistant UI + Vercel AI SDK, /chat page)
  |  POST /api/ask/chat/completions      same origin, Better Auth cookie
  v
web server (TanStack Start, src/server.ts)
  |  proxyApi(): strip cookie, add short-lived identity bearer, stream the body
  v
deepfix-api  POST /ask/chat/completions      member auth, limits, thread store
  |  the tool loop: LiteLLM chat.completions(stream=True, tools=[search_documents])
  |  search_documents -> QueryService.retrieve (champion config, cutoff)
  v
AI SDK UI message stream (x-vercel-ai-ui-message-stream: v1)
  start / start-step / text-* / tool-input-* / tool-output-available /
  finish-step / data-deepfix-grounding / data-deepfix-turn / finish / [DONE]
```

**Chosen: the loop is Python, in the API.** The reasons, in the order they
matter:

- **Security.** `LLM_API_KEY` stays in one process, as today
  (`Settings.LLM_API_KEY`, read by `RAGWorkflow._get_llm`). A `streamText` route
  inside the web server would need that key in the Node process, which is the
  process that also renders HTML for every visitor and proxies every
  `/api/*` call. `web/src/lib/server-config.ts` deliberately holds only the auth
  secret and the API base URL; adding a model key there widens the blast radius
  of an SSR bug and contradicts the deployed topology, where `docker-compose.yml`
  gives `web` no model credentials at all.
- **Observability.** Every answer already traces to MLflow through
  `trace_request(...)` in `src/deepfix_rag/tracing.py` and every vote mirrors to
  an assessment through `src/deepfix_rag/feedback.py`. Both need the
  interaction id and the trace id in one transaction with the answer. A Node
  loop would have to call back into Python to record, and the trace would no
  longer be the thing that produced the text.
- **Evaluability.** `deepfix eval` and `apply_candidate` score retrieval through
  `RAGWorkflow` with the champion config, retrieval-only
  (`promotion.py::_scoring_config` forces `retrieval_only=True`). If the tool ran
  in Node, the served retrieval path and the scored retrieval path would be two
  implementations of one contract, and the optimizer would be optimising
  something the product does not run.
- **Reuse.** `src/deepfix_rag/api/chat_stream.py` already builds the exact parts
  the AI SDK accepts, `src/deepfix_rag/api/chat_turns.py` already keeps a live
  turn registry for resume, and `chat-page.tsx` already sits on Assistant UI.
- **Honest abort.** The retrieval is a local call inside the API. A Node tool
  would abort an HTTP request to Python and leave the Python side running.

**Rejected: a TS route in the web server.** It is not impossible — `streamText`
with a `tools` map and a `fetch` to `POST /retrieval` is a small file. It loses
on the five points above. It also cannot honour FR-006 (persist the tool's
passages as the turn's `interaction_hits`) without a second round trip that the
browser could skip, and it would put the citation/grounding decision in a place
with no access to the cutoff that produced the passages.

**What still crosses to the front end as an AI SDK *tool*.** The loop executing
in Python does not make the tool invisible: the stream carries real
`tool-input-available` and `tool-output-available` parts with
`toolName: "search_documents"`. The client sees each call, renders each result,
and can rebuild the sources panel from the message parts after a reload. The
tool's *schema* is declared once, in Python, and mirrored by a Zod schema in
`web/src/schemas/ask-chat.ts` that validates frames exactly as
`web/src/schemas/admin-chat.ts` does today.

### D-2: `search_documents` is the only tool, it always runs, and it returns the cutoff's survivors

The tool's contract is declared once, as `SEARCH_DOCUMENTS_SCHEMA` in
`src/deepfix_rag/ask_chat.py`. The OpenAI-style function definition the loop
sends (`SEARCH_DOCUMENTS_TOOL`) is built from that same object at import time, so
the schema the provider validates against and the schema the tests and the loop
read cannot be two different things.

```jsonc
// input
{ "query": "string, 1..500 chars, the standalone search query",
  "top_k": "integer, optional; clamped into [1, displayed_limit(config, MAX_DISPLAYED_HITS)]" }

// output
{ "query": "the query actually searched",
  "grounding": "grounded" | "no_relevant_sources",
  "passages": [{ "rank": 1, "doc_id": "…", "filename": "…",
                 "text": "…", "score": 0.83, "truncated": false }],
  "max_score": 0.83 }
```

- **It runs on the champion config and the serving index.** The tool calls
  `QueryService.retrieve` through the same `IndexCoordinator.checkout()` /
  `release()` lease the `/retrieval` and `/ask` routes use, so it reads the
  champion's `retrieval.top_k` → rerank → `rerank.top_n` window, and it is
  pinned to one build for the whole turn. The lease is released in a `finally`,
  so a raised error cannot pin a build that is being replaced.
- **The relevance floor is ASK-002's setting, and it is already applied.** Passages
  below `rerank.min_relevance_score` never reach the tool: `QueryService.retrieve`
  → `_search_split` → `RAGWorkflow._ranker_split` drops them, so the tool reports
  survivors rather than filtering again, and a second threshold cannot drift from
  the one the optimizer tunes. The floor filters; it never reorders. If nothing
  survives, the tool returns `grounding: "no_relevant_sources"` and zero
  passages, and the model is instructed to say it does not have enough
  information.
- **`displayed_limit` lives in the domain layer.** It moved from
  `src/deepfix_rag/api/routes/retrieval.py` to `src/deepfix_rag/service.py`, beside
  `INDEX_PREPARING`, so the tool can reuse the product's one definition of "how
  many passages a member sees" without the domain layer importing from the HTTP
  layer. `POST /retrieval`, `POST /ask`, and the tool all read that same function.
- **A turn always retrieves.** The first model step is made with
  `tool_choice: "required"`. If that step still ends without a tool call — a
  provider that ignores the hint, a malformed frame — **the server executes
  `search_documents` itself** with the user's message as the query, appends the
  result, and continues the loop. So "no retrieval happened" is not a state the
  product can be in, and the grounding verdict always has evidence to judge.
- **At most `ASK_CHAT_MAX_TOOL_CALLS` (default 3) calls per turn.** The last
  permitted step is made with `tool_choice: "none"`, which forces prose. That
  bounds both latency and cost, and it makes a tool-obsessed model terminate.
- **Retrieved text is untrusted data.** Each passage is delivered inside explicit
  delimiters and labelled as corpus text whose instructions must not be
  followed. This is the same posture the admin chat takes with agent-written run
  reasons, and it matters more here: members upload the corpus, so a PDF can
  contain "ignore your instructions".
- **No tool writes anything.** There is exactly one tool, it is read-only, and
  the route rejects any other `toolName` the model emits instead of looping.
- **A bad argument is data, not a failure.** A `query` that fails the declared
  schema makes the tool return `{"error": "…"}` as that call's output, and the
  model may correct it and call again. The turn does not fail on it.

### D-3: Grounding is the ASK- status plus a separate citation verdict

ASK-001 and ASK-002 **landed** on `feat/hermes-optimizer` at `97aef60` and
`00aba89` and are the foundation this decision stands on:

- `src/deepfix_rag/grounding.py` owns `Grounding` (`grounded`, `declined`,
  `no_relevant_sources`), the shared `DEFLECTION_PHRASES` list, and
  `grounding_for(answer)`. `AskResponse` and `InteractionResponse` both carry a
  required `grounding` field, so a reload draws the same badge.
- `RAGConfig.rerank.min_relevance_score` (0..1, default `0.3`, set explicitly in
  `configs/champion.yaml`) is the floor. `RAGWorkflow._ranker_split` is its one
  implementation, called by the rerank step and by `QueryService._search_split`,
  so eval and the live routes agree.

This PRD consumes that enum and **does not add a value to it**, because a wider
enum would collide with the code that now derives and stores it.

Instead, a turn persists two things:

| Field | Values | Meaning |
| :--- | :--- | :--- |
| `grounding` | `grounded`, `no_relevant_sources`, `declined` | `grounded` requires at least one passage at or above `rerank.min_relevance_score` to have been delivered to the model. `no_relevant_sources` means the floor dropped everything. `declined` means passages were available and `grounding.grounding_for(answer)` matches a `DEFLECTION_PHRASES` entry or one of its `DEFLECTION_PATTERNS`. |
| `citations` | `[{ marker, rank, doc_id }]`, validated | The `[n]` markers the answer actually used, each resolved to a delivered passage at or above the cutoff. A marker that resolves to nothing, or to a filtered-out passage, is dropped and counted. |

The table's meaning column is this PRD's reading of what the ASK- statuses mean
for a tool-using turn. If the ASK- implementation defines `declined` differently
— for example as an explicit model decision rather than a phrase match — the
route adopts its definition and only the badge copy in FR-009 changes; the
schema and the citation half do not.

The badge rule, which is what the member sees: **"Grounded" only when
`grounding == "grounded"` and `citations` is non-empty.** `grounded` with no
valid citation renders as "Partly grounded" in the warning style, because an
answer that had the evidence and did not point at it is a defect worth seeing,
not a success to celebrate. This satisfies "Grounded only when the answer uses
passages that pass the cutoff" without touching the ASK- enum.

### D-4: Stop cancels the model stream, keeps the text, and keeps the evidence

The browser's `Chat` abort closes the fetch. Starlette then **closes the relay
generator**, which unwinds the loop as a `GeneratorExit` — not as an `Exception`,
so `except Exception` never sees it. That single fact shaped the implementation:
the relay's terminal state is written in a `finally`, and the cancelled path is
caught explicitly. The first attempt used `except Exception` alone and left the
row `running`; a real-socket test is what caught it, and a `TestClient` never
could, because it cannot abort mid-stream.

Then:

- The partial assistant text is kept and stored, written on every delta so a
  resume replays exactly what the member had read. A stopped turn is readable,
  not lost.
- Tool results already obtained are kept, because they are written as each search
  completes rather than at the end. The sources panel survives Stop.
- The turn is stored `cancelled` with `ended_at`, and a `data-deepfix-turn` part
  carrying that status is still emitted before the stream closes, so the UI and a
  later resume agree.
- **The retrieval is cancelled, and the model call must be too.** Today
  `CustomLiteLLM.achat` is `asyncio.to_thread(self.chat)`: cancelling the task
  abandons the *wait*, not the HTTP request to OpenRouter, which runs to
  completion and bills every token. The tool loop therefore calls
  `litellm.acompletion(..., stream=True)` directly and closes the async
  generator in a `finally`. This is a requirement, not an implementation
  detail: without it Stop does not stop cost.
- **`drop_params` is set once, at import.** `litellm_additional_kwargs()` calls
  `configure_litellm_drop_params()`, which assigns the process-wide
  `litellm.drop_params` flag; `src/deepfix_rag/llm.py` already calls it on import.
  The loop reads the flag rather than re-setting it per model step, because a
  global side effect inside a request is a race with any other request in the
  same process. The effective value is identical.
- Stop never deletes a turn, never cancels anything else, and never touches a
  feedback vote.

Regenerate follows the admin chat's settled rule: the AI SDK's reload trigger
replaces the newest answered turn **in place**. Votes recorded on the replaced
answer are cleared through the existing `clear` action, so a member's own
downvote on a discarded answer does not linger as an assessment on a trace
nobody sees. **No new vote action is introduced**: `feedback_events.action` has
`CHECK(action IN ('up', 'down', 'clear'))`, and adding a fourth value would need
the table rebuild D-5 exists to avoid. `clear` already deletes the mirrored MLflow
assessment (`mlflow.delete_assessment` in `feedback.py`), which is exactly the
intent.

Two rules the first draft left implicit and the implementation had to settle:

- **A running turn is never replaced.** Regenerate resolves its target from the
  newest turn in the store, and if that turn is `running` the request answers
  **409** rather than replacing a reply that is still being written. Without the
  check, a reload pressed twice would race two writers onto one answer.
- **A `failed` turn is a valid target.** FR-018 offers regenerate for a failed
  answer, so `failed` must be replaceable; only `running` is refused. The target
  set is `completed`, `failed`, `cancelled`.
- **A regenerate reuses the turn's one `interactions` row.** The row is opened
  before the first byte and rewritten in place, so a regenerate does not create a
  second row and the thread's join stays one-to-one (FR-019).

### D-5: Threads are server-side, per member; localStorage is imported once

New tables in the same SQLite database the API already uses
(`settings.product_db_path`, via `deepfix_rag.storage.connect`), as built:

| Table | Columns |
| :--- | :--- |
| `ask_chat_threads` | `thread_id` PK, `title`, `owner_id`, `owner_email`, `created_at`, `updated_at`, `legacy_session_id` |
| `ask_chat_turns` | `turn_id` PK, `thread_id`, `owner_id`, `seq`, `status` (`running`/`completed`/`failed`/`cancelled`), `retry_of`, `error`, `grounding`, `citations` (JSON), `retrieval_query`, `first_token_ms`, `started_at`, `ended_at` |

`legacy_session_id` is the browser session a thread was imported from, and it is
what makes FR-033 idempotent **per session id** rather than per payload: a member
who loads two browsers must not duplicate the first browser's threads. It is
nullable, because a thread created in the product has no legacy session. It was
added by ASKCHAT-009 with the same `PRAGMA table_info` + `ALTER TABLE … ADD COLUMN`
guard the `interactions` columns use, so an existing database gains it in place.

**The turn table holds turn state only.** The question and the answer are *not* in
`ask_chat_turns`: they live in the `interactions` row the turn links to through
`interactions.turn_id`, because FR-019 requires exactly one such row per assistant
turn and it must be the row votes, history, the MLflow mirror, and the export read.
The two reads a thread load makes therefore join it — a thread load is the thread,
its turns, and each turn's `interactions` row:

```sql
-- turns by thread: the state columns, plus the answer the interaction holds
SELECT t.turn_id, t.thread_id, t.owner_id, t.seq, t.status, t.retry_of, t.error,
       t.grounding, t.citations, t.retrieval_query, t.first_token_ms, t.started_at,
       t.ended_at, i.interaction_id, i.query AS question, i.answer
FROM ask_chat_turns AS t
LEFT JOIN interactions AS i ON i.turn_id = t.turn_id
WHERE t.thread_id = ? AND t.owner_id = ? ORDER BY t.seq
```

A `LEFT JOIN`, not an inner one: a turn that is still `running` has no interaction
yet, and it must load with nulls rather than disappear from the thread. The
one-to-one shape is guaranteed by FR-019's single-row rule, not by the query.

`GET /ask/chat/threads/{thread_id}` also returns each turn's **stored passages**,
read from that interaction's `interaction_hits`. This was not in the first draft
and is required for FR-007: a reload has no `tool-output-available` part to read,
so the stored hit snapshot is the only way to rebuild the sources panel without a
request per answer.

`interactions` gains two nullable columns, added with `ALTER TABLE … ADD COLUMN`
the way `user_email` was: `thread_id` and `turn_id`. Nothing else about that
table changes — **in particular the `kind` CHECK stays
`('retrieval', 'ask')`.** Each assistant turn writes one `interactions` row with
`kind='ask'`, which is what keeps `feedback.py`, `history.py`,
`export_feedback`, the MLflow assessment mirror, and the optimizer's dev/holdout
split working without a migration or a second code path. A new kind would force
a table rebuild and would hide member chat from the admin history screen.

Those two columns have **one writer**: `interactions.link_interaction`, which
carries the ownership check. The row is inserted before the first byte (so a
cancelled turn can be read at all) and filled in afterwards by
`interactions.replace_interaction`, which replaces its hits wholesale because a
regenerate may have found different passages.

Titles are derived (first 120 characters of the first user message, collapsed to
one line). Isolation is by `owner_id` on every read and write; another
member's thread id answers 404, exactly as `GET /interactions/{id}` does today.

**Two current behaviours in this area are open decisions, not settled design.**
They are documented in "Open questions" and were deliberately not changed in the
documentation pass:

- `DELETE /ask/chat/threads/{thread_id}` answers **404** when the thread is
  already gone, where the admin chat answers an idempotent 204. A retried delete
  therefore looks like a failure to a client that does not special-case it.
- `ask_chat.rename_thread` trims the title and *then* measures it against the
  120-character bound, while the route's Pydantic field caps the raw string. A
  title of 120 characters plus trailing spaces is accepted by the store and
  refused by the route, so the two disagree at the edge.

**localStorage**: imported once per member, best effort. `deepfix.chat_sessions.v2:<ownerId>`
holds sessions whose turns carry `interactionId` values that already exist in the
database. The import creates one thread per session, one turn per stored turn,
and links them by interaction id; a turn whose interaction no longer exists, or
belongs to someone else, is skipped and counted rather than invented. The wire
payload is **snake_case** (`interaction_id`, `created_at`), and the request model
accepts the retired page's camelCase (`interactionId`, `createdAt`) as aliases, so
an older browser's payload is still understood. After an import the keys are
deleted whatever the outcome, with one notice if it failed — no member is left on a
store the server cannot see, and nothing is silently duplicated.

### D-6: The served retrieval is the scored retrieval

The tool reads the champion config and the serving index through the same
`QueryService.retrieve` the rest of the API uses. `deepfix eval` and the
promotion gate keep scoring retrieval-only through `RAGWorkflow`. A parity test
(ASKCHAT-002) asserts the tool's passage list **equals**
`QueryService.retrieve(query)` truncated at the number of survivors of the
cutoff — same ids, same order, same scores. So a champion promoted by the
optimizer changes what the chat retrieves, by construction, and no second
retrieval contract exists to drift.

Two consequences worth stating plainly:

- The cutoff is a *filter over the scored window*. `eval.cutoff_k` and the
  cutoff score are different things and neither moves the other.
- `deepfix eval` stays single-turn and retrieval-only, so **single-turn eval
  remains valid for a multi-turn product**: retrieval quality per question is
  unchanged by the chat, and that is what the gate scores. Generation metrics
  continue to be measured on the single-turn `RAGWorkflow` path, which is now a
  second generation path (D-2's tool loop is the first). The divergence is
  recorded as a risk (R-3), with the mitigation that the system prompt and model
  are shared and the eval path is not the one that serves members.

### D-7: Models are unchanged; the chat resolves `LLM_MODEL` and there is no Ask-chat model setting

Decided by the product owner on **2026-11-15**. This decision replaces the
earlier proposal of an `ASK_CHAT_MODEL` setting that fell back to `LLM_MODEL`
when empty; that proposal is described below so the reasoning is on record.

- Generation: `LLM_MODEL` = `qwen/qwen3.8-flash`, as `example.env` ships it.
  **Verified today** against OpenRouter's model list: `qwen/qwen3.8-flash`
  advertises `tools` and `tool_choice` in `supported_parameters`, with a
  1,000,000-token context. Tool calling is therefore supported by the model that
  is already configured, and no model change is needed for this feature.
- **No `ASK_CHAT_MODEL` setting.** The Ask chat resolves `LLM_MODEL` directly,
  the same value `deepfix eval` and the single-turn `POST /ask` already use. One
  model, one place to change it, and no setting that can silently diverge from
  what the promotion gate scores. This is the owner's decision and it replaces
  the earlier proposal of a chat-specific setting falling back to `LLM_MODEL`
  (mirroring `HERMES_CHAT_MODEL` → `HERMES_MODEL`), which is recorded here so a
  reader can see what was considered and rejected.
- If a live turn fails tool calling in practice, the manual fallback is to point
  `LLM_MODEL` at a model that advertises `tools` — `qwen/qwen3.8-27b` is the
  documented candidate. This is an operator action, not a setting this feature
  adds, and it moves generation for the chat, `POST /ask`, and eval together on
  purpose. `example.env` today ships no commented alternative for `LLM_MODEL`
  (checked: lines 28–40 hold `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
  `LLM_JUDGE_MODEL`, `DEEPFIX_LLM_DROP_PARAMS` and nothing else), so ASKCHAT-005
  adds **one comment line** beneath `LLM_MODEL` naming `qwen/qwen3.8-27b` as the
  tool-calling fallback. A comment, not a variable: no new `Settings` field, no
  new Compose entry, and the `ASK_CHAT_.*MODEL` guard below still passes.
- No model id is written in Python or TypeScript.
- Embeddings `perplexity/pplx-embed-v1-4b` and rerank `cohere/rerank-4-fast`:
  unchanged, and no setting is added for them.

### D-8: Limits sized for members on one 7.6 GB host

The admin chat's limits assume one admin. Ask has many members, one small
process, and no new container.

| Setting | Default | Why |
| :--- | :--- | :--- |
| `ASK_CHAT_MAX_TURNS_PER_WINDOW` | 20 | Per member per 300 s, counted from `ask_chat_turns` rows. Enough for a fast back-and-forth, low enough that one member cannot hold the API. |
| `ASK_CHAT_TURN_WINDOW_SECONDS` | 300 | Same window as the admin chat, so operators learn one number. |
| `ASK_CHAT_MAX_CONCURRENT_TURNS` | 4 | Global. The API also serves uploads, index reads, and the admin chat. |
| `ASK_CHAT_MAX_TOOL_CALLS` | 3 | Caps a turn at four model steps. |
| `ASK_CHAT_MAX_HISTORY_MESSAGES` | 12 | Six exchanges. Enough for pronouns and ellipsis; bounded prompt cost. |
| `ASK_CHAT_MAX_MESSAGE_CHARS` | 4000 | A question, not a document. |
| `ASK_CHAT_MAX_PASSAGE_CHARS` | 1200 | Truncation of one passage sent to the model, with `truncated: true` so the UI can say so. |
| `ASK_CHAT_TURN_TIMEOUT_SECONDS` | 120 | A chat reply, not a training job. |

**No new service.** The loop runs inside the existing `backend-api` container
(no `mem_limit`, per `docs/deployment.md`), reads the index the process already
holds, and adds no resident memory beyond in-flight request state. The caps that
matter stay untouched: `hermes` at 4 GB with `cpus: "2"`, `mlflow` at 2 GB,
`rustfs` and `diagnostic-agent` at 1 GB each. Nothing here raises a cap or adds a
container, and no setting gets a literal outside `Settings`.

### D-9: Deleting a thread keeps its `interactions` rows and their votes

Decided by the product owner on **2026-11-15**. This replaces the open question
"what happens to a member's votes when a thread is deleted?".

A thread delete removes the conversation: the thread row and its `ask_chat_turns`
rows, children before parent, in one transaction. It does **not** remove the
`interactions` rows those turns produced, nor their `interaction_hits`, nor any
`feedback_events`, `feedback_mirrors`, or `feedback_comments` row.

Why: those rows are the optimizer's dataset. `build_questions` reads
`feedback_events` joined to `interactions`, and `is_holdout` derives holdout
membership from the normalized query text. A delete that cascaded would shrink
the dataset mid-flight, could move a holdout question out of the split, and would
let a member change what the optimizer is scored on by tidying their history. The
member loses the conversation, not the record of their judgement. FR-010 carries
this, and ASKCHAT-001's integration test asserts it.

The consequence to know about: a deleted thread's interactions remain visible in
the admin history screen (`GET /admin/interactions`), exactly as a single-turn
`ask` interaction does today. That is intended — the admin view is the feedback
record, not the member's conversation.

### D-10: A grounded but uncited answer shows as "Partly grounded"

Decided by the product owner on **2026-11-15**. This replaces the open question
"should a turn be allowed to answer with no citation when passages were found?".

Yes, it is allowed. ASK-001 landed the badge as a `Record<Grounding, …>` in
`web/src/features/chat/grounding-copy.ts` with two distinct labels —
`grounded` → "Grounded", and both `declined` and `no_relevant_sources` → "Not
found in the documents" — plus `groundingNote`, which separates the last two by
their note ("Answer declined" / "No matching passages"). This decision adds
**one** rendered state on top and changes nothing else:

| Grounding | Citations | Badge | Note |
| :--- | :--- | :--- | :--- |
| `grounded` | non-empty | **Grounded** (landed copy) | `N passages used` (landed) |
| `grounded` | empty | **Partly grounded** (new, warning style) | `N passages used` |
| `declined` | — | **Not found in the documents** (landed copy) | `Answer declined` (landed) |
| `no_relevant_sources` | — | **Not found in the documents** (landed copy) | `No matching passages` (landed) |

Why: the model is instructed to cite, and a non-compliant answer is a quality
defect rather than a product state. Turning a model slip into a member-visible
error would punish the member for the model's mistake, and refusing the answer
would throw away text that was in fact grounded. FR-009 carries the rule and
ASKCHAT-008's test asserts both the badge and that a citation marker which
resolves to nothing renders as plain text, never as a link.

The "Partly grounded" count is a quality signal to watch: Success Metrics tracks
that no turn is labelled Grounded without a valid citation, and the partly-
grounded rate is observable from `ask_chat_turns.citations`.

### D-11: Threads are unbounded, with a 50-item list cap and no prune

Decided by the product owner on **2026-11-15**. This replaces the open question
"how long should a thread list be allowed to grow?".

There is no retention window in this release: no
`ASK_CHAT_THREAD_RETENTION_DAYS`, no prune job, no scheduled deletion. A member's
threads stay until the member deletes them. `GET /ask/chat/threads` returns the
newest 50 and nothing paginates beyond that, which is a display cap rather than a
lifecycle rule — the older threads still exist and are still reachable by id.

Why: a thread is text only — a few kilobytes per turn — and the pilot has tens of
members. Adding a prune would add a deletion path, a setting, and a class of bug
(a member's history disappearing) for no measured pressure. `admin_chat.prune_audit`
exists because the audit table is an append-only security log that grows with
every action regardless of anyone's intent; a member's own conversations are
bounded by their own usage and are theirs to delete.

The conditions that would reopen this: `ask_chat_turns` or `ask_chat_threads`
becoming a measurable share of `product_db_path`, or a member accumulating
thousands of threads. The mechanism would be an
`ASK_CHAT_THREAD_RETENTION_DAYS` prune on the existing optimization scheduler
tick, exactly as `admin_chat.prune_audit` does — one story, no schema change.

## Glossary

- **Turn** — one user message plus the assistant answer produced for it, with its
  tool calls. Identified by `turn_id`.
- **Thread** — an ordered list of turns owned by one member. Identified by
  `thread_id`.
- **Tool** — the one callable the model may make, `search_documents`. Read-only,
  returns ranked passages.
- **Passage** — one retrieved chunk: `doc_id`, filename, text, score, rank. The
  same object the Ask page's passages drawer already shows as a hit.
- **Cutoff** — `rerank.min_relevance_score` in `RAGConfig` (ASK-002, default 0.3).
  Passages below it are dropped inside the retrieval path, before the model sees
  them. Used interchangeably with "relevance floor"; both mean this one setting.
- **Grounding status** — one of `grounded`, `no_relevant_sources`, `declined`
  (D-3).
- **Citation** — a `[n]` marker in the answer that resolves to a delivered
  passage at or above the cutoff.
- **Interaction** — the `interactions` row that records the turn for votes,
  history, MLflow, and the optimizer's feedback export. One per assistant turn,
  `kind='ask'`.
- **Champion** — `configs/champion.yaml` plus `indexes/current`; written only by
  `promotion.apply_candidate`.
- **UI message stream** — the Vercel AI SDK's SSE protocol
  (`x-vercel-ai-ui-message-stream: v1`).

## Goals and non-goals

### Goals

- **G1** A member reads an answer as it is written, and can stop it.
- **G2** The next question can rely on the last one: follow-ups retrieve with the
  conversation in view and get answered with it.
- **G3** The retrieved passages are the model's input and the member's evidence:
  one artefact, shown in a sources panel built from tool results, each passage
  votable, and a grounding verdict that is true of the answer.
- **G4** A conversation is a server object: it survives a reload, a second
  browser, and an API restart, and is private to its owner.
- **G5** Every turn remains traceable and votable exactly as today, so the
  optimizer's feedback dataset keeps growing with the same semantics.
- **G6** The evaluation path, the holdout split, the promotion gate, and
  `apply_candidate` are untouched, and single-turn eval stays valid.
- **G7** No credential reaches the browser, no model id is hardcoded, and no new
  service, container, or memory cap is added.

### Non-goals

- Multi-turn evaluation, conversation-level metrics, or a new promotion gate.
- Chat-driven configuration changes or optimization runs (that is the admin
  Hermes chat's job, and its gate forbids promotion).
- A second model, a model picker, or per-turn model choice. The chat resolves
  `LLM_MODEL` and nothing else (D-7).
- Editing or branching a conversation ("edit this earlier message and re-run").
- Attachments, images, voice, or dictation on Ask.
- Replacing `POST /ask`, `POST /retrieval`, or the Search page.

## Personas / actors

- **Member** — a signed-in user of the shared workspace. Asks questions, reads
  answers, stops them, votes on answers and passages, keeps a history. Cannot
  see another member's threads.
- **deepfix-api** — runs the tool loop, the cutoff, the thread store, the
  limits, the traces, and the interaction rows. Holds every credential.
- **Web server (BFF)** — adds the short-lived identity bearer and streams the
  body unchanged. Holds no model credential.
- **Optimizer (Hermes)** — reads `interactions`, `feedback_events`, and
  `export_feedback` to build dev and holdout sets; writes the champion only
  through `apply_candidate`.
- **Operator** — deploys and verifies; tunes the `ASK_CHAT_*` settings.

## Scope

### In scope

- `POST /ask/chat/completions` streaming the AI SDK UI message stream, with the
  tool loop.
- `search_documents` over the champion config and the cutoff.
- Server-side threads: create, list, get, rename, delete, hydrate; per member.
- Stop, resume, regenerate, error states.
- Passage votes and answer votes per turn, unchanged endpoints.
- One MLflow trace per turn with child spans per tool call.
- A rewritten Ask page on `useAISDKRuntime` and one `Chat` per thread, with a
  sources panel, a grounding badge, and citation links.
- A one-time import of existing localStorage sessions.

### Out of scope (this release)

- Conversation-level evaluation, and any change to `deepfix eval`, the gate, or
  `apply_candidate`.
- Sharing a thread, exporting it, or making it public.
- Thread search, pinning, folders, or pagination beyond "newest 50".
- A client-side tool execution path (`onToolCall` in the browser).
- Any change to the admin Hermes chat, the Search page, or the Optimization
  screen, other than the shared module refactors D-1 requires.

### Won't (explicitly, forever, not just this release)

- A browser tab or a browser bundle holding `LLM_API_KEY`, `OPENROUTER_API_KEY`,
  `COHERE_API_KEY`, `EMBEDDING_API_KEY`, or an S3 secret.
- A model id hardcoded in Python or TypeScript.
- The model answering a corpus question with no retrieval having run.
- A tool other than the read-only `search_documents`, or a tool that writes.
- Members reading each other's threads, or a trace carrying another member's
  conversation text.
- Retrieval for a turn reading anything other than the serving (champion) index,
  or any path into holdout.
- A second always-on service, a queue, a worker process, or a new container.

## User journeys

### J1 — Ask, watch it stream, stop it (FR-001, FR-002, FR-005, FR-014, FR-029)

1. A member opens `/chat`, picks a thread (or the empty state) and types a
   question. The composer sends `POST /api/ask/chat/completions` with the thread
   id and the message.
2. The status line shows "Searching the corpus…" from the first
   `tool-input-available` part, then "Read 5 passages", then the answer's words
   arrive progressively.
3. The member presses **Stop**. The fetch aborts, the API closes the model
   stream, marks the turn `cancelled`, and keeps the partial text and the five
   passages. The answer stays on screen with a "Stopped" marker; the sources
   panel is still open and still votable.
4. The member presses the send control again and the next turn continues the
   same thread.

### J2 — A follow-up that needs the earlier turn (FR-010, FR-011, FR-012, FR-004)

1. The member asks "What did the budget committee decide about the harbour
   extension?" and reads the answer.
2. The member asks "And who voted against it?". The server rebuilds the thread's
   last six exchanges, and the model calls `search_documents` with a standalone
   query such as "harbour extension budget committee opposing votes" — its own
   phrasing, with the history in view — instead of the bare pronoun sentence.
3. The passages come back above the cutoff, the answer cites `[1]` and `[3]`,
   and the badge reads **Grounded**. The tool query is stored as
   `retrieval_query`, so this turn can become a usable dev question as well
   (FR-021).
4. The member reloads the page: the same two turns render from the store, with
   the same sources, on any browser they sign in from.

### J3 — No relevant sources, and a regenerate (FR-009, FR-015, FR-017)

1. The member asks something the corpus does not cover. The tool returns zero
   passages above the cutoff and `grounding: "no_relevant_sources"`.
2. The answer says "I do not have enough information" — a phrase from
   `DEFLECTION_PHRASES`, so the evaluator counts it as a refusal rather than an
   answer — and the badge reads **Not found in the documents** with "No matching
   passages" beside it, instead of Grounded.
3. The member presses **Regenerate**. The browser's reload trigger replaces this
   answer in place; the turn id is stable, the previous answer's text does not
   appear beside the new one, and any vote on the replaced answer is cleared with
   the existing `clear` action, so no assessment points at text nobody can see.

## Functional requirements

Priority is **Must** (the release is incomplete without it), **Should** (planned,
may slip one release), or **Could** (nice, unplanned).

| ID | Requirement | Priority | Rationale |
| :--- | :--- | :--- | :--- |
| FR-001 | The agent loop must run in deepfix-api. The browser must reach it only through the same-origin `/api/ask/chat/*` path on the existing BFF proxy, must receive no model key and no model id, and must receive the AI SDK UI message stream (`x-vercel-ai-ui-message-stream: v1`) as `src/deepfix_rag/api/chat_stream.py` already emits it. | Must | G7, D-1. One credential holder; the admin chat proves the protocol works through this proxy. |
| FR-002 | The stream must carry these parts, in **this** grammar: `start` once; then, **per model step**, `start-step`, a `text-start`/`text-delta`*/`text-end` group for whatever prose that step produced, and `finish-step`; a tool call's `tool-input-start`, `tool-input-available`, and `tool-output-available` are emitted between one step's `finish-step` and the next step's `start-step`, because they belong to the boundary between steps; then one `data-deepfix-grounding`, one `data-deepfix-turn`, `finish`, and `data: [DONE]`. `error` may appear in place of the per-step prose when a step fails. Nothing is buffered: the first delta reaches the browser when the model produced it. | Must | G1, D-1. The AI SDK's parser and Assistant UI's message parts are what render this; anything else needs a hand-written renderer. A two-step turn emits **two** `start-step`/`finish-step` pairs, so the sequence is a grammar rather than one fixed list — the first draft's single linear order was not emittable. |
| FR-003 | `POST /ask/chat/completions` must accept `{thread_id, messages}` for a signed-in member, rebuild the conversation from the store, and answer 401 unsigned, 404 for another member's thread, 429 over a limit, and 503 while the index is preparing, each with a sentence the UI shows. | Must | G4, G7. Same contract the admin chat's route already has, with the member's role instead of admin. |
| FR-004 | `search_documents` must be the only tool, must run on the serving index through `QueryService.retrieve` with the champion config, must return only what survives the landed `rerank.min_relevance_score` floor (which that path already applies — the tool reports, it does not re-filter), and must return the passage shape in D-2. Its depth must come from the existing `displayed_limit(config, MAX_DISPLAYED_HITS)` helper — `rerank.top_n` when rerank is on, `retrieval.top_k` otherwise, then the display cap — and a requested `top_k` must be clamped into `[1, displayed_limit(...)]`, never widening what the single-turn product would show. | Must | G3, D-2, G6. The floor is the grounding decision and it already lives in one place; the tool's window must be the product's window, not a second one. |
| FR-005 | Every turn must retrieve. The first model step must be sent with `tool_choice: "required"`; if that step ends with no tool call, the server must execute `search_documents` itself with the user's message, append the result, and continue. A turn must make at most `ASK_CHAT_MAX_TOOL_CALLS` tool calls and the last permitted step must be sent with `tool_choice: "none"`. An unknown `toolName` must be rejected, not executed. | Must | G3, D-2. Otherwise "no retrieval happened" is representable, and the grounding verdict becomes a guess. |
| FR-006 | The passages a turn's tool returned must be persisted as that turn's `interaction_hits`, ranked in tool order, so `POST /feedback` with `target: "passage"` and a rank keeps working with no new endpoint, and so reloading the thread restores the panel from the store. The **full** passage text must be stored even when the text sent to the model was truncated at `ASK_CHAT_MAX_PASSAGE_CHARS`, because `passage_key` is a hash of the text and `build_questions` groups on it: storing the truncated text would give a chat passage a different key from the same chunk's single-turn passage, and the two votes would never combine. | Must | G5, D-5. Votes are keyed on `(interaction_id, rank)`, so the ranks must be the ones the member sees, and the passage identity must be the one the optimizer already knows. |
| FR-007 | The sources panel must be rendered from the `tool-output-available` parts of the message, never from parsing the answer's prose, and must survive a reload by being rebuilt from the stored message parts. | Must | G3. A prose parser breaks on the first table or footnote and would disagree with what the model saw. |
| FR-008 | The system prompt must require `[n]` citation markers for every factual claim drawn from a passage; the server must validate each marker against the passages delivered in that turn, keep only markers that resolve to a passage at or above the cutoff, persist them in `ask_chat_turns.citations`, and emit them in `data-deepfix-grounding`. | Must | G3, D-3. A citation is only meaningful if it resolves to something the model was given. |
| FR-009 | Each turn must persist `grounding` using the landed `Grounding` enum (`grounded`, `declined`, `no_relevant_sources`) and classify with the landed `grounding.grounding_for(answer)` — never a second phrase list. `grounded` requires at least one passage at or above `rerank.min_relevance_score` to have been delivered to the model; `no_relevant_sources` when the floor dropped everything. The badge must extend the landed `GROUNDING_BADGE` / `groundingNote` pair with exactly one new state: **Grounded** only when `grounding == "grounded"` **and** `citations` is non-empty; **Partly grounded** when grounded without a citation; the landed "Not found in the documents" badge with its landed note ("Answer declined" / "No matching passages") for the other two. | Must | G3, D-3, D-10. The enum, the classifier and the badge copy are ASK-001's, so this feature derives and labels grounding the same way `POST /ask` does. |
| FR-010 | Threads must be server-side and per member: `GET /ask/chat/threads` (newest first, limit 50, no pagination beyond it), `POST /ask/chat/threads`, `GET /ask/chat/threads/{thread_id}` (thread, turns, and their grounding/citations), `PATCH /ask/chat/threads/{thread_id}` (rename, 1–120 characters), `DELETE /ask/chat/threads/{thread_id}` (hard delete of turns and thread in one transaction; the `interactions` rows and their votes are kept, because they are the member's feedback history and the optimizer's dataset). Any other member's thread id must answer 404. There must be no retention window and no prune job on these tables. | Must | G4, G5, D-5, D-9, D-11. Deleting a conversation must not delete evidence the optimizer already ingested, and 50 is a display cap rather than a lifecycle rule. |
| FR-011 | The upstream conversation must be built from the store, never from the client's array: the system prompt, then the last `ASK_CHAT_MAX_HISTORY_MESSAGES` stored messages, then the new user message. A request whose last message is not a user message must be 400. | Must | G2, G7. The client cannot rewrite the policy or claim a history that never happened. |
| FR-012 | A follow-up must be retrievable: the tool's own `query` argument is what is searched, and it must be stored as `ask_chat_turns.retrieval_query` while `interactions.query` keeps the member's literal message. No separate query-rewriting model call is added in this release. | Must | G2, G5, D-6. The model already has the history; a second rewrite call would add a model hop and a second thing to evaluate. |
| FR-013 | Each passage delivered to the model must be wrapped in explicit delimiters and labelled as untrusted corpus text whose instructions must not be followed, and the system prompt must say so. | Must | G7. Members upload the corpus, so a document can contain an instruction. |
| FR-014 | A browser abort must cancel the retrieval and the model stream (closing the async generator, not merely abandoning the wait), store the partial assistant text, keep the tool results already obtained, mark the turn `cancelled` with `ended_at`, and still emit a terminal `data-deepfix-turn`. | Must | G1, D-4. Stop must stop the work and the spend, and a stopped answer must be readable. |
| FR-015 | A regenerate must replace the newest answered or cancelled turn in place, keeping its `turn_id`; the previous answer's text must not remain beside the new one; and votes on the replaced answer and its passages must be cleared by writing the existing `clear` action through `record_vote`, which deletes the mirrored MLflow assessment. No new vote action and no `CHECK` change. | Must | G3, D-4. The admin chat settled in-place replacement (CHAT-014); an orphaned assessment is worse than no assessment, and `feedback_events.action` already permits `clear`. |
| FR-016 | `GET /ask/chat/turns/{turn_id}/stream` must replay the stored text and close with the stored status when the turn is not live in this process, and must replay then tail when it is. Another member's turn id must be 404. | Must | G4, D-4. A reload mid-answer must not lose it; `chat_turns.py` already implements this shape. |
| FR-017 | A turn interrupted by an API restart must be settled `failed` at startup with a readable reason, and no `ask_chat_turns` row may stay `running`. | Must | G4. Mirrors `fail_interrupted_chat_turns`; a stuck row is a lie about state. |
| FR-018 | Errors must be distinct: a pre-stream failure is a JSON status with a sentence; a mid-stream failure is an `error` part followed by a terminal `data-deepfix-turn` with `status: "failed"`; the UI must offer regenerate for a failed turn and must not offer it for a `no_relevant_sources` answer. | Must | G1. A blank stream and a refused question must not look the same. |
| FR-019 | Each assistant turn must write exactly one `interactions` row with `kind='ask'`, `thread_id`, `turn_id`, the answer, the grounding status, and its hits; `interaction_hits` must hold the tool's passages. The `kind` CHECK constraint must not be changed. | Must | G5, D-5. This is what keeps votes, history, the assessment mirror, and `export_feedback` working with no new path. |
| FR-020 | Each turn must produce one MLflow trace in the `deepfix_product` experiment through `trace_request`, tagged `interaction_id`, `thread_id`, `turn_id`, and `grounding`, with one child span per tool call carrying the tool query, the passage count, the max score, and the latency. A tracing failure must never fail the turn. | Must | G5. The optimizer reads these traces; a tool call must be visible in them. |
| FR-021 | `build_questions` must group on the turn's standalone query: `_latest_events` gains a `LEFT JOIN ask_chat_turns` and selects `COALESCE(t.retrieval_query, i.query)` as the question text, normalised by the existing `normalize_query`. `interactions.query` keeps the member's literal message, which is what the history screen and the answer-vote rows show. `is_holdout` keeps hashing that normalised text with the same 20% rule, and `_answer_downvotes` applies the same coalesce. | Must | G5, G6, D-6. "And who voted against it?" is not a usable dev question; its standalone tool query is, and the join is where the optimizer's read happens. |
| FR-022 | `POST /ask`, `POST /retrieval`, `GET /interactions/{id}`, the Search page, the history screens, the admin Hermes chat, and their existing tests must be unchanged, except that the single-turn `POST /ask` route may write the new `thread_id`/`turn_id` columns as `NULL`. | Must | G6, G7. No regression to shipped surfaces. |
| FR-023 | `promotion.apply_candidate` must remain the only writer of `configs/champion.yaml` and `indexes/current`; no ask-chat path may call it, write those paths, or read holdout. | Must | G6, G7. The gate is not negotiable. |
| FR-024 | `deepfix eval`, `run_evaluation`, the promotion gate, and their settings must be unchanged, and a test must assert that the tool's passage list equals `QueryService.retrieve`'s output truncated at the cutoff survivors — same ids, order, and scores. | Must | G6, D-6. Served and scored retrieval must be one implementation. |
| FR-025 | Generation must stay `LLM_MODEL` (`qwen/qwen3.8-flash`) through `litellm.acompletion`, resolved directly with no Ask-chat model setting; embeddings and rerank settings must not change; and no model id may appear in Python or TypeScript. | Must | G7, D-7. The model already advertises tool calling, and one model id in one place cannot diverge from what the gate scores. |
| FR-026 | Rate limits must be enforced from `ask_chat_turns` rows: `ASK_CHAT_MAX_TURNS_PER_WINDOW` per member per `ASK_CHAT_TURN_WINDOW_SECONDS`, and `ASK_CHAT_MAX_CONCURRENT_TURNS` globally; a breach must be 429 with `Retry-After` and a sentence naming the wait. | Must | G7, D-8. Members outnumber admins and share one API process. |
| FR-027 | Per turn, `ASK_CHAT_MAX_MESSAGE_CHARS`, `ASK_CHAT_MAX_HISTORY_MESSAGES`, `ASK_CHAT_MAX_TOOL_CALLS`, `ASK_CHAT_MAX_PASSAGE_CHARS`, and `ASK_CHAT_TURN_TIMEOUT_SECONDS` must bound the request, the prompt, the loop, one passage, and the wall clock; exceeding a bound must be a readable 400 or a terminal error part, never a hang. | Must | G7, D-8. A chat reply is not a training job. |
| FR-028 | No new container, service, worker, or memory cap may be added; every `ASK_CHAT_*` setting must exist on `Settings`, appear in `docker-compose.yml`'s `backend-api` environment with a non-empty default, and be documented in `example.env`. No `ASK_CHAT_*` model setting may exist (FR-025, D-7). The `hermes`, `mlflow`, `rustfs`, and `diagnostic-agent` caps must not change. | Must | G7, D-8. The host has 7.6 GB and `backend-api` has no cap of its own. |
| FR-029 | The Ask page must stream the answer into the message column, show the send control becoming **Stop** while a turn runs, offer regenerate on a finished or failed answer, keep a per-thread runtime so a reply in flight does not move between threads, and render the thread list from the server. | Must | G1, G4, D-1. The admin chat's per-thread `Chat` is what fixed this exact bug there (CHAT-015). |
| FR-030 | The sources panel must list each tool passage with its filename, rank, score, text, and vote controls, and must open on the passages for the selected answer; a citation marker in the answer must focus the passage it names. The streaming region must be `aria-live="polite"`, every control must have a name, and focus must return to the composer after a turn. | Must | G1, G3. A keyboard and a screen reader must reach Stop and the evidence. |
| FR-031 | The page must reuse the existing shared components rather than growing second copies: `web/src/components/chat/markdown.tsx` (`ChatMarkdown`) and `web/src/components/chat/composer.tsx` (`ChatComposerBar`, `ChatComposerHint`); the stream-part builders in `src/deepfix_rag/api/chat_stream.py`, extended with `tool-input-*` and `tool-output-*`; and the per-thread `Chat` transport pattern from `web/src/features/admin-chat/chat-transport.ts`. | Must | G1, G7. CHAT-016 already made one markdown renderer and one composer bar; a second of either is the drift this requirement forbids. |
| FR-032 | The browser's only stream reader must be one module, `web/src/services/ask-chat-sse.ts`, added to `api-boundary.test.ts`'s `EXEMPT` set by its relative path (`join("services", "ask-chat-sse.ts")`), and it must name only `/api/ask/chat/...` targets. | Must | G7. The generated client cannot parse a stream; the exemption list is how that is kept to one file. |
| FR-033 | The Ask page's existing localStorage sessions (`deepfix.chat_sessions.v2:<ownerId>`, `deepfix.active_session_id.v2:<ownerId>`) must be imported once into server threads by linking each stored turn to its existing `interactionId`, skipping turns whose interaction is missing or belongs to another member, and then deleting both keys. | Should | G4, D-5. Cheap to do, and losing a member's history on upgrade is a visible regression. |
| FR-034 | The answer's "no relevant sources" copy must be reachable without the model: when the tool returned nothing above the cutoff, the route must still produce a complete, correct turn (status, badge, terminal part) even if the model output is unusable. | Should | G3. The refusal is the product's answer, not the model's favour. |

No FR is unlinked to a goal (see Traceability), and no FR contradicts a non-goal.

## Non-functional requirements

- **NFR-1 Performance.** First token must not wait for the whole answer: the
  route streams each `text-delta` as LiteLLM yields it, and adds no buffering
  beyond one frame. A retrieval tool call is one `QueryService.retrieve` on the
  warm index; its latency is bounded by the existing 600 s query-workflow
  timeout, and the practical target is the same order as today's `/retrieval`.
  A thread load must be two indexed reads (threads by owner, turns by thread);
  a test asserts `EXPLAIN QUERY PLAN` reports an index search for both, and that
  indexes exist on `ask_chat_threads (owner_id, updated_at DESC)` and
  `ask_chat_turns (thread_id, seq)`. No wall-clock assertion is made in CI.
- **NFR-2 Reliability.** No `ask_chat_turns` row may remain `running` after a
  restart (FR-017). A dropped connection is a normal end, logged once, not an
  exception. A tracing or feedback failure never fails a turn — it is logged and
  the turn completes, exactly as `trace_request` behaves today.
- **NFR-3 Resource limits.** At most `ASK_CHAT_MAX_CONCURRENT_TURNS` (4) turns
  run at once, at most 4 model steps each, with one bounded prompt. No new
  container, no queue, no worker. `hermes` 4 GB / 2 CPUs, `mlflow` 2 GB,
  `rustfs` 1 GB, `diagnostic-agent` 1 GB are unchanged, and `backend-api` keeps
  no `mem_limit`, as `docs/deployment.md` records.
- **NFR-4 Security.** Every `/ask/chat/*` route requires a valid identity token
  and scopes every read and write by owner id; another member's ids are 404, not
  403, so existence is not leaked. The browser holds no credential: the BFF
  strips `cookie` and `authorization` and adds a short-lived bearer
  (`web/src/api-proxy.ts`, `identityToken`). Retrieved corpus text is untrusted
  input (FR-013) and cannot widen the tool surface (one read-only tool). No route
  accepts a URL that could redirect the upstream call: the base URL is
  configuration only. Redaction reuses `optimization.py`'s `_secrets` / `_redact`,
  whose `_SECRET_ENVS` tuple already covers `LLM_API_KEY`, `OPENROUTER_API_KEY`,
  both Hermes keys, and the S3 secret; it is extended with `EMBEDDING_API_KEY`
  and `COHERE_API_KEY`, which are not in the tuple today.
- **NFR-5 Privacy.** Conversation text is stored in the API's SQLite store, which
  already holds feedback text and run reasons. It is not copied to S3, not
  written into the audit-style rows, and MLflow tracing keeps the same content
  posture it has today (inputs and outputs for `ask`, no conversation dump added
  beyond what a turn already records). Holdout content is never in scope of any
  ask-chat read. Deleting a thread removes its turns and its thread row; the
  `interactions` rows it produced stay, because they are the member's feedback
  record and the optimizer's dataset (FR-010).
- **NFR-6 Accessibility.** The streaming region is `aria-live="polite"`; the
  composer, Stop, regenerate, thread list, rename, delete, and vote controls are
  named buttons reachable by keyboard with visible focus; the badge states are
  text, not colour alone; error text is announced as `role="alert"`, matching the
  existing pages.
- **NFR-7 Compatibility.** Assistant UI stays on `@assistant-ui/react` 0.15 with
  `@assistant-ui/react-ai-sdk` and the Vercel AI SDK (`ai` 7,
  `@ai-sdk/react` 4) it ships with; no second chat runtime is added. The web app
  keeps the existing proxy, the generated client, and one origin. Python stays
  3.12 with the repo's `X | Y` and built-in generic style.
- **NFR-8 Operability.** One structured log line per turn start, per tool call,
  and per terminal state, in the style the API already uses, with the turn id,
  the thread id, the status, the tool-call count, and the first-token latency.
  No log line contains a credential, a passage's text, or a private path.
- **NFR-9 Compliance.** N/A for a new regulatory obligation: this release adds no
  new personal-data category (a member's id and email are already stored by
  Better Auth and on `interactions`), no payment data, and no special-category
  data. The applicable existing obligation is data minimisation, which NFR-5
  satisfies. Revisit if threads are ever shared or exported.

## Acceptance criteria (release level)

1. A member asks a question on `/chat` and reads the answer progressively, with
   the send control replaced by **Stop** while it runs.
2. Stop ends the answer, keeps the text and the passages it had retrieved, marks
   the turn `cancelled`, and leaves the thread usable.
3. A follow-up with a pronoun is answered from the earlier turns' context, and
   its stored `retrieval_query` is a standalone question.
4. Every answer arrives with a sources panel built from tool results, each
   passage votable, and the vote appears in `feedback_events` and on the trace.
5. The badge reads Grounded only when passages above the cutoff were delivered
   and at least one is cited; a question outside the corpus reads No relevant
   sources and answers with a `DEFLECTION_PHRASES` refusal.
6. Reloading mid-answer resumes it; reloading after it shows the whole thread on
   another browser; another member's thread id is a 404.
7. Regenerate replaces the answer in place and does not leave two answers.
8. The optimizer's data is intact: the same champion config and index are what
   the tool reads, `apply_candidate` is still the only writer, `deepfix eval`
   and the gate are unchanged, single-turn eval still scores, and a member's
   votes still become dev or holdout questions using the standalone tool query.
9. No browser payload contains a model id or a credential; no model id literal
   exists in Python or TypeScript.
10. The 21st question in five minutes from one member is refused with a
    readable 429; a fifth concurrent turn across the deployment is refused and
    says to try again.
11. No new container, service, or memory cap exists, and every `ASK_CHAT_*`
    setting is in `Settings`, in `docker-compose.yml`, and in `example.env` —
    and there is no `ASK_CHAT_MODEL`: the chat resolves `LLM_MODEL`.
12. `uv run --env-file .env pytest -m "not network and not e2e"`,
    `uv run ruff check`, `uv run ruff format --check`, `bun run typecheck`,
    `bun run lint`, `bun run test`, and `bun run build` are green in `web/`.

## Dependencies & assumptions

### Dependencies

- **The ASK- work, which has landed.** ASK-001 (`97aef60`), ASK-002 (`00aba89`)
  and ASK-006 (`223a122`) are on `feat/hermes-optimizer` and this PRD builds on
  them without replacing them: the `Grounding` enum and
  `grounding.grounding_for` in `src/deepfix_rag/grounding.py`, the
  `rerank.min_relevance_score` floor and `RAGWorkflow._ranker_split`, the
  `grounding` field on `AskResponse` and `InteractionResponse`, and the
  per-session `pendingBySession` map in `web/src/features/chat/chat-page.tsx`.
  The stories consume those names as they exist. No story renames a field or
  re-implements the floor.
- **The admin Hermes chat's infrastructure**, which is in the tree: the AI SDK
  UI message stream builders (`src/deepfix_rag/api/chat_stream.py`), the live
  turn registry (`src/deepfix_rag/api/chat_turns.py`), the per-thread `Chat`
  transport (`web/src/features/admin-chat/chat-transport.ts`), the shared
  `ChatMarkdown` / `ChatComposerBar` (`web/src/components/chat/`), and the
  Assistant UI composer with Stop (`chat-composer.tsx`).
- **The existing product data path**: `src/deepfix_rag/interactions.py`
  (`record_interaction`, `get_interaction`), `src/deepfix_rag/feedback.py`
  (`record_vote`, `current_votes`, `build_questions`, `export_feedback`,
  `FeedbackWorker`), `src/deepfix_rag/tracing.py` (`trace_request`), and
  `src/deepfix_rag/documents.py` (`document_filenames`).
- **The index coordinator**: `IndexCoordinator.checkout()` / `release()` /
  `snapshot()`, so a turn is pinned to one build while it streams.
- **The deployed topology**: `docker-compose.yml`'s `backend-api` environment
  allowlist (no `env_file`), the `web` service with no model credentials, and
  `docs/deployment.md`'s 7.6 GB budget.
- **OpenRouter**: `qwen/qwen3.8-flash` advertises `tools` and `tool_choice`
  (checked against the live model list on 15 November 2026).

### Assumptions (labelled)

- **(resolved, 2026-11-15)** The ASK- work landed with exactly those names:
  `Grounding.GROUNDED` / `DECLINED` / `NO_RELEVANT_SOURCES` in
  `src/deepfix_rag/grounding.py`, and `rerank.min_relevance_score` in
  `RAGConfig`. Verified in the tree at `223a122`; no rename is needed and
  ASKCHAT-002 consumes the setting as-is.
- **(assumption)** `qwen/qwen3.8-flash` honours `tool_choice: "required"` and
  `"none"` through OpenRouter. Its `supported_parameters` list both, but the
  behaviour is not measured here; ASKCHAT-003 proves it against a local fake
  endpoint and ASKCHAT-003 records one live smoke result. The FR-005 server-side
  fallback means a provider that ignores the hint degrades to "the server
  searched anyway", not to a wrong answer.
- **(assumption)** `litellm.acompletion(stream=True)` closes the upstream
  connection when the async generator is closed, so Stop stops the spend. If it
  does not, the turn timeout bounds it and R-2 records the residual cost.
- **(decided, 2026-11-15)** The chat has no model setting of its own: it resolves
  `LLM_MODEL`, the same value eval and `POST /ask` use (D-7). A model that fails
  tool calling is handled by an operator pointing `LLM_MODEL` at
  `qwen/qwen3.8-27b`, not by a new variable.
- **(assumption)** The API keeps running one uvicorn worker, so the in-process
  turn registry (`chat_turns.py`) is enough for live resume; a second worker
  degrades to stored-text replay, as it does for the admin chat.
- **(assumption)** Member volumes are small (tens of concurrent members on one
  pilot VPS), which is why four concurrent turns is a defensible default rather
  than a queued design.
- **(assumption)** A member's existing localStorage sessions carry usable
  `interactionId` values, which the current code writes
  (`ChatTurn.interactionId`); FR-033 skips any that do not resolve.

## Risks & mitigations

| # | Risk | Impact | Mitigation |
| :--- | :--- | :--- | :--- |
| R-1 | **Stop does not stop the spend.** The current `CustomLiteLLM.achat` runs the call in a thread, so cancelling the task abandons the wait, not the request; a stopped turn keeps billing to `max_tokens`. | Cost grows with how often members change their mind, invisible to them. | FR-014 requires the loop to use `litellm.acompletion(stream=True)` and close the generator on cancel; `ASK_CHAT_TURN_TIMEOUT_SECONDS` bounds the worst case; ASKCHAT-004's test closes a real local connection and asserts the fake server saw its stream closed, not merely that the client stopped reading. |
| R-2 | **The provider ignores the abort.** Even with a closed generator, an upstream request may finish server-side. | Residual token cost per stopped turn. | Accepted and bounded: the loop's `max_tokens` comes from the champion's `generation.max_tokens` (4096), the turn timeout is 120 s, and ASKCHAT-004 records the measured residual against the fake endpoint and one live smoke. |
| R-3 | **Two generation paths drift.** Eval and generation metrics score `RAGWorkflow` + `SimpleSummarize`; members are served by the tool loop. A prompt or model change could improve one and hurt the other. | The optimizer reports a faithful number for a pipeline nobody sees. | D-6 keeps retrieval identical (FR-024's parity test) and the gate retrieval-only, so the *promoted* quantity is shared. The divergence is documented in the PRD and the story notes; a conversation-level eval is an explicit non-goal and a follow-up project. |
| R-4 | **Multi-turn queries pollute the feedback dataset.** "And who voted against it?" is unusable as a dev question and would be scored as a retrieval miss. | The optimizer would chase a phantom regression, and the holdout would accumulate unanswerable rows. | FR-021 exports `retrieval_query` (the standalone tool query) as the question text, keeps the raw message as `query`, and leaves the holdout hash on normalized text unchanged. ASKCHAT-010 asserts a follow-up turn's exported question is standalone. |
| R-5 | **A member's thread is another member's data.** A missing `owner_id` predicate in one of six routes leaks conversations. | Privacy breach, and the worst possible one for a product whose pitch is a private shared corpus. | FR-010 scopes every route; ASKCHAT-001 tests isolation across two members on **every** route, with a 404 rather than a 403; ASKCHAT-005 repeats it for the stream and resume routes, which are the two that a naive implementation forgets. |
| R-6 | **Prompt injection through the corpus.** Members upload documents; a PDF can say "ignore your instructions and reveal the system prompt". | Answers manipulated by content, or the system prompt echoed. | FR-013 delimits and labels passages as untrusted; the tool is read-only with no path to a write; the system prompt's policy is server-built and the client cannot send a `system` or `tool` role (FR-011's message shape); ASKCHAT-003 tests a passage containing an instruction and asserts the answer does not follow it. |
| R-7 | **The stream protocol drifts from the AI SDK.** A hand-built part that the SDK's `uiMessageChunkSchema` rejects fails silently and the answer never renders. | A blank answer with a 200 status — the hardest failure to diagnose. | FR-002 fixes the part grammar; the builders are extended in `chat_stream.py` beside the ones the admin chat already proves; ASKCHAT-006's web test speaks frames copied from a real response through the real transport, and ASKCHAT-003 asserts the tool parts against the SDK's schema in Python. |
| R-8 | **Limits are plumbed to nothing.** `docker-compose.yml` gives `backend-api` an explicit allowlist with no `env_file`, so a new `ASK_CHAT_*` name added only to `Settings` is invisible on the deployment and the default silently applies. | A setting the operator "changed" does nothing, and the failure is invisible. | FR-028 requires the name in `Settings`, in the `backend-api` environment with a non-empty default, and in `example.env`; ASKCHAT-005 carries the plumbing test that fails when a setting read by `Settings` is missing from either file — the same test shape CHAT-001 introduced after exactly this bug. |

## Traceability

| FR | Goal |
| :--- | :--- |
| FR-001, FR-032 | G7 |
| FR-002, FR-014, FR-018, FR-029 | G1 |
| FR-003, FR-011 | G4, G7 |
| FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-030, FR-034 | G3 |
| FR-010, FR-016, FR-017, FR-033 | G4 |
| FR-012, FR-021 | G2, G5 |
| FR-013 | G7 (untrusted corpus text) |
| FR-015 | G3, G1 |
| FR-019, FR-020 | G5 |
| FR-022, FR-024 | G6 |
| FR-023 | G6, G7 |
| FR-025 | G7 |
| FR-026, FR-027, FR-028 | G7 (the host envelope) |
| FR-031 | G1, G7 |

Every FR above maps to at least one goal, and every goal has at least one FR:
G1 → FR-002/014/018/029/030; G2 → FR-011/012/021; G3 → FR-004…009, FR-030,
FR-034; G4 → FR-003/010/016/017/033; G5 → FR-006/019/020/021; G6 → FR-022/023/024;
G7 → FR-001/003/011/013/023/025/026/027/028/031/032.

## Story to FR map

| Story | FRs |
| :--- | :--- |
| ASKCHAT-001 | FR-010, FR-017, FR-019 (the schema half) |
| ASKCHAT-002 | FR-004, FR-023, FR-024 |
| ASKCHAT-003 | FR-001, FR-002, FR-003, FR-005, FR-006, FR-008, FR-009, FR-011, FR-012, FR-013, FR-019 (the row half), FR-025, FR-031, FR-034 (the server half) |
| ASKCHAT-004 | FR-014, FR-015, FR-016, FR-018 |
| ASKCHAT-005 | FR-026, FR-027, FR-028 |
| ASKCHAT-006 | FR-029 (the data layer), FR-031, FR-032 |
| ASKCHAT-007 | FR-018 (the regenerate control), FR-029 (the page) |
| ASKCHAT-008 | FR-007, FR-030, FR-034 (the badge half) |
| ASKCHAT-009 | FR-033 |
| ASKCHAT-010 | FR-020, FR-021, FR-023 (the guard test) |
| ASKCHAT-011 | all, verified on the deployment |

FR-022 (no regression on the shipped surfaces) has no single owner on purpose:
every story's definition of done runs the unchanged suites, and ASKCHAT-011
verifies it end to end. It is the one FR that is a property of the whole set.

## User stories

Stories are ordered, one implementation session each on `feat/hermes-optimizer`,
each independently testable and committed on its own. This document does not
check them off; `create-story` turns each into its own file under
`tasks/stories-ask-chat/` with a `*-validation.md` sibling, as the admin chat did.

`create-prd` says a PRD carries no stories and that `create-story` owns them.
This repository keeps its story list in the PRD so the story-to-FR map is in one
place (`tasks/prd-admin-hermes-chat.md` does the same), and the ids below are
`ASKCHAT-*` rather than `US-*`/`S-*` for that reason.

Each story's tests are integration or end-to-end: a real SQLite file, a real
FastAPI app, a real local HTTP model endpoint, and the real AI SDK transport.
No `unittest.mock`, per `AGENTS.md`. Narrow unit tests of helpers are not the
acceptance evidence; the story's definition of done names the end-to-end test.

### ASKCHAT-001: The ask-chat store and the thread routes

**Description:** As a member, I want my conversations kept on the server and
private to me, so a reload or another browser shows the same history.

**Depends on:** nothing (the store is additive and needs no ASK- work).
**Blocks:** ASKCHAT-003, ASKCHAT-004, ASKCHAT-005, ASKCHAT-006.

**Acceptance criteria:**

- `src/deepfix_rag/ask_chat.py` owns the schema and every read and write, using
  `deepfix_rag.storage.connect` and `settings.product_db_path`, and exposes
  `init_ask_chat(db_path)` called from the API lifespan next to
  `init_admin_chat`.
- `ask_chat_threads` and `ask_chat_turns` exist exactly as D-5 defines them, with
  `CREATE TABLE IF NOT EXISTS`, a `status` CHECK over the four values, and the
  two indexes NFR-1 names (`ask_chat_threads (owner_id, updated_at DESC)`,
  `ask_chat_turns (thread_id, seq)`).
- `interactions` gains nullable `thread_id` and `turn_id` columns via
  `ALTER TABLE … ADD COLUMN` guarded by a `PRAGMA table_info` check, exactly as
  `user_email` was; the `kind` CHECK is unchanged and a test asserts
  `INSERT … kind='ask_chat'` is still rejected.
- `create_thread`, `list_threads(owner_id, limit=50)`, `get_thread`,
  `rename_thread(title 1..120)`, `delete_thread`, `append_turn`,
  `open_turn`, `finish_turn`, `set_grounding`, `count_recent_turns` are typed
  functions; every one takes `owner_id` and filters on it.
- `DELETE` is one transaction: the thread's turns, then the thread row. The
  `interactions` rows for those turns, their `interaction_hits`, and every
  `feedback_events` / `feedback_mirrors` / `feedback_comments` row are **not**
  deleted, and a test asserts the member's votes survive a thread delete (D-9).
- There is no prune: no `ASK_CHAT_THREAD_RETENTION_DAYS` setting, no scheduler
  hook, and a test asserts a thread older than any plausible window still lists
  and still loads (D-11).
- Routes: `GET /ask/chat/threads`, `POST /ask/chat/threads`,
  `GET /ask/chat/threads/{thread_id}`,
  `PATCH /ask/chat/threads/{thread_id}`, `DELETE /ask/chat/threads/{thread_id}`,
  all member-authenticated, with `operation_id`s, `error_responses`, and the
  generated client regenerated and committed.
- `fail_interrupted_ask_turns(db_path)` runs in the lifespan next to
  `fail_interrupted_chat_turns` and settles a `running` row `failed` with a
  readable reason.
- **Integration test (`tests/test_ask_chat_threads.py`)** with two members and a
  real SQLite file through the real app: create, list ordering, rename, get,
  delete; every route answers 404 for the other member's id; a deleted thread
  leaves its `interactions`, `interaction_hits`, `feedback_events`, and
  `feedback_mirrors` rows intact, and the surviving vote is still returned by
  `current_votes` and still exported by `build_questions`; a restart between two
  requests settles the `running` turn `failed`; a thread whose `updated_at` is
  years old still lists and still loads; `EXPLAIN QUERY PLAN` for the two load
  queries reports an index search.

### ASKCHAT-002: `search_documents` over the champion index

**Description:** As the model, I want one read-only tool that returns the ranked
passages that survive the cutoff, so an answer can be grounded in exactly what
the optimizer scores.

**Depends on:** the ASK- minimum-relevance setting. **Blocks:** ASKCHAT-003.

**Acceptance criteria:**

- `search_documents` is declared in `src/deepfix_rag/ask_chat.py` with the JSON
  schema in D-2 and one OpenAI-style function definition built from it, so the
  schema has exactly one source.
- It retrieves through `QueryService.retrieve` under an
  `IndexCoordinator.checkout()` lease, pins the build id for the whole turn, and
  releases it in a `finally`.
- It drops every passage below `rerank.min_relevance_score` — by relying on
  `QueryService.retrieve`, which already applies the floor through
  `_search_split` → `RAGWorkflow._ranker_split`, not by re-comparing scores —
  returns `grounding: "no_relevant_sources"` with an empty list when nothing
  survives, clamps `top_k` into `[1, displayed_limit(config, MAX_DISPLAYED_HITS)]`
  using the existing helper in `api/routes/retrieval.py`, and truncates a
  passage's text at `ASK_CHAT_MAX_PASSAGE_CHARS` with `truncated: true`.
- **Parity test, integration, on the real sample index** (`populated_sample_index_dir`,
  `MockEmbedding`): for a set of queries the tool's `(doc_id, rank, score)`
  triples equal `QueryService.retrieve(query)` truncated to `displayed_limit` —
  same ids, same order, same scores, no rerun, no reordering. This is FR-024 and
  it fails loudly if anyone "improves" the tool's retrieval.
- A test with `rerank.min_relevance_score` raised above every score returns
  `no_relevant_sources` and an empty list from **both** the tool and
  `QueryService.retrieve`, because the floor lives inside the path they share.
- A test with `rerank.enabled: false` returns the `retrieval.top_k` window capped
  by `displayed_limit` — the same list the Search page shows — so the tool is
  correct on a champion without rerank and cannot offer the model 50 passages.

### ASKCHAT-003: The tool loop, streamed as the AI SDK UI message stream

**Description:** As a member, I want my answer to arrive as it is written, having
searched the corpus first, so I can read it and see what it was based on.

**Depends on:** ASKCHAT-001, ASKCHAT-002. **Blocks:** everything after it.

**Acceptance criteria:**

- `POST /ask/chat/completions` (`operation_id="ask_chat_completion"`) returns a
  `StreamingResponse` with `media_type="text/event-stream"` and the
  `x-vercel-ai-ui-message-stream: v1` header, streams the parts FR-002 lists, and
  ends with `data: [DONE]`.
- The loop is `litellm.acompletion(model=…, messages=…, tools=[search_documents],
  tool_choice="required"|"auto"|"none", stream=True)` with `drop_params` from
  `litellm_additional_kwargs()`, the model resolved from `require_model("LLM_MODEL")`
  through the existing `resolve_litellm_model(...)` helper — the same two calls
  `RAGWorkflow._get_llm` makes — and never from a literal or a second setting.
- Step 1 uses `tool_choice: "required"`; a step that ends with no tool call while
  the turn has made none causes the server to run `search_documents` with the
  user's message, append the result, and continue (FR-005). A turn stops calling
  tools at `ASK_CHAT_MAX_TOOL_CALLS` and the next step uses
  `tool_choice: "none"`. An unknown `toolName` ends the turn with an error part.
- Tool parts are emitted as `tool-input-start`, `tool-input-available`
  (`{toolCallId, toolName: "search_documents", input}`) and
  `tool-output-available` (`{toolCallId, output: <D-2 output>}`), and
  `chat_stream.py` gains those builders beside the existing ones rather than a
  second module.
- The system prompt is server-built: it forbids writing a `system` or `tool`
  role from the client, requires `[n]` citations, requires the refusal phrase on
  an empty result, and wraps each passage as untrusted data (FR-013). History
  comes from the store, last `ASK_CHAT_MAX_HISTORY_MESSAGES` messages (FR-011).
- One `interactions` row per turn (`kind='ask'`, `thread_id`, `turn_id`), its
  hits are the tool's passages in rank order (FR-006), and `data-deepfix-grounding`
  carries `{grounding, citations, cutoff, max_score}`, with `citations` validated
  against the delivered passages (FR-008, FR-009).
- **Integration test** (`tests/test_ask_chat_stream.py`) against a **local fake
  OpenAI-compatible endpoint** (a real `uvicorn` server on a loopback port,
  `LLM_BASE_URL` pointed at it, no mocks) and the real sample index: the test
  asserts, over a real socket, the exact part sequence for (a) tool call → text,
  (b) no tool call on step 1 → the server searched → text, (c) empty tool result
  → refusal text and `no_relevant_sources`, (d) a passage containing "ignore your
  instructions" → the answer does not echo it and the system prompt is absent
  from the stream.
- A test parses every emitted part through the AI SDK's chunk schema (a JSON
  schema fixture committed beside the test) and fails on an unknown `type`.
- FR-034's server half: with the tool returning nothing above the cutoff and the
  fake endpoint returning an unusable body (empty string, then a malformed frame),
  the route still produces a complete turn — `no_relevant_sources`, zero
  citations, a terminal turn part, and a stored `interactions` row — and never
  hangs or leaves the turn `running`.
- `web/openapi.json` is regenerated and committed; `bun run generate:api`
  produces a client that typechecks.

### ASKCHAT-004: Stop, resume, regenerate, and error states

**Description:** As a member, I want to stop an answer, come back to it, and ask
again, without losing what was already written.

**Depends on:** ASKCHAT-003.

**Acceptance criteria:**

- On disconnect, the generator cancels the in-flight retrieval and closes the
  LiteLLM async generator; the partial text and any tool results already
  received are stored, the turn is `cancelled` with `ended_at`, and a terminal
  `data-deepfix-turn` with `status: "cancelled"` is emitted before the stream
  closes.
- `GET /ask/chat/turns/{turn_id}/stream` replays the stored text as
  `text-start` / one `text-delta` / `text-end` and closes with the stored status
  and grounding when the turn is not live here, and replays then tails when it
  is; another member's turn is 404.
- A regenerate (`regenerate: true`) replaces the newest answered or cancelled
  turn in place, reuses its `turn_id`, leaves no second answer in the thread, and
  clears votes on the replaced answer and its passages through the existing
  `clear` action (which also deletes the mirrored MLflow assessment).
- A mid-stream model failure emits an `error` part then a terminal turn part with
  `status: "failed"`; the interaction row records the error sentence and the
  member can regenerate.
- **Integration test** against the fake endpoint and the real app: a client that
  closes the socket mid-stream leaves a `cancelled` row with the partial text and
  the tool's hits, and the fake server records that its response stream was
  **closed** (not merely that the client stopped reading); a resume of that turn
  returns the stored text once and then the terminal frame; a regenerate leaves
  one assistant message and a `clear` event per prior vote; a failure frame
  yields `failed` and a regenerate that succeeds.

### ASKCHAT-005: Limits, the turn budget, and the plumbing

**Description:** As an operator, I want one member and one deployment bounded, and
every bound visible where I configure it.

**Depends on:** ASKCHAT-003 (rows to count), ASKCHAT-004.

**Acceptance criteria:**

- `ASK_CHAT_MAX_TURNS_PER_WINDOW`, `ASK_CHAT_TURN_WINDOW_SECONDS`,
  `ASK_CHAT_MAX_CONCURRENT_TURNS`, `ASK_CHAT_MAX_TOOL_CALLS`,
  `ASK_CHAT_MAX_HISTORY_MESSAGES`, `ASK_CHAT_MAX_MESSAGE_CHARS`,
  `ASK_CHAT_MAX_PASSAGE_CHARS`, and `ASK_CHAT_TURN_TIMEOUT_SECONDS` exist on
  `Settings` with the D-8 defaults, appear in `docker-compose.yml`'s
  `backend-api` environment with non-empty defaults
  (`${ASK_CHAT_MAX_TOOL_CALLS:-3}` and so on), and are documented in
  `example.env`. **No model setting is added:** there is no `ASK_CHAT_MODEL`, and
  a test asserts no `Settings` field and no `example.env` variable line matches
  `ASK_CHAT_.*MODEL`, so the D-7 decision cannot be quietly reversed by a later
  story. The one `example.env` change is a comment beneath `LLM_MODEL` naming
  `qwen/qwen3.8-27b` as the manual tool-calling fallback — a comment, not a
  variable, so the guard above still passes.
- The plumbing test also asserts the chat's model comes from `LLM_MODEL`: with
  `LLM_MODEL` set to a fixture id and `Settings()` built, the loop's resolved
  model id is that fixture — no default, no fallback, no second variable.
- The limiter counts `ask_chat_turns` rows for the member in the window, then
  running turns globally, and refuses with 429, `Retry-After`, and a sentence
  naming the wait; a refusal writes no turn row, so it cannot ratchet itself.
- A turn exceeding `ASK_CHAT_TURN_TIMEOUT_SECONDS` ends with an error part and
  `status: "failed"`, and its row is not left `running`.
- A message over `ASK_CHAT_MAX_MESSAGE_CHARS`, a history over
  `ASK_CHAT_MAX_HISTORY_MESSAGES` (trimmed, not refused), or a last message that
  is not a user message is handled as FR-011 and FR-027 specify.
- **Integration test**: 20 turns in a window pass and the 21st is 429 with
  `Retry-After`; a fifth concurrent turn is 429 while four are open; a slow turn
  hits the timeout and settles `failed`; a 400 for a 4001-character message; a
  composed `docker-compose.yml` with every `ASK_CHAT_*` unset still builds
  `Settings()`, and a test fails when a setting read by `Settings` is missing
  from the Compose file or `example.env`; the model guard holds — `Settings` has
  no field and `example.env` no variable line matching `ASK_CHAT_.*MODEL`, and a
  turn streamed against the fixture endpoint reports the `LLM_MODEL` fixture id
  as its model.

### ASKCHAT-006: The web data layer and the per-thread transport

**Description:** As a developer, I want one typed layer between the page and the
stream, so the page holds no protocol knowledge.

**Depends on:** ASKCHAT-001 … ASKCHAT-005 (all routes exist).

**Acceptance criteria:**

- `web/src/schemas/ask-chat.ts` holds the Zod frame contracts: the turn frame,
  the grounding frame, and the `search_documents` output shape, mirroring
  `web/src/schemas/admin-chat.ts`.
- `web/src/services/ask-chat-sse.ts` is the only module in `web/src` that reads
  an event stream, targets only `/api/ask/chat/...`, and is added to
  `api-boundary.test.ts`'s `EXEMPT` set as `join("services", "ask-chat-sse.ts")`.
- `web/src/services/ask-chat-service.ts` wraps the generated client for the five
  thread routes and delegates the two streaming calls to the reader.
- `web/src/features/chat/ask-chat-transport.ts` creates one `Chat` per thread with
  `AssistantChatTransport`, `prepareSendMessagesRequest` sending `{thread_id,
  messages, regenerate}`, and `prepareReconnectToStreamRequest` targeting the
  resume path — the shape `features/admin-chat/chat-transport.ts` already proves.
- **Test (`web/src/test/ask-chat-stream.test.ts`)**: frames copied from a real
  streamed response, through the real transport, produce one assistant message
  with a text part, two `tool-search_documents` parts with their outputs, and the
  grounding data part; a second `Chat` for another thread does not inherit the
  first's messages; a reload seeds the stored messages and does not draw the
  answer twice.

### ASKCHAT-007: The Ask page becomes a streaming chat

**Description:** As a member, I want the page to read like a conversation:
streaming answers, a Stop control, a thread list I can name and delete.

**Depends on:** ASKCHAT-006.

**Acceptance criteria:**

- `chat-page.tsx` runs on `useAISDKRuntime` with one `Chat` per thread, keeps its
  per-thread session map, and no longer calls `askService.ask` for a new question.
- The send control becomes **Stop** while the turn runs (`ComposerPrimitive.Cancel`,
  as `features/admin-chat/chat-composer.tsx` does), and the composer bar and hint
  remain `ChatComposerBar` / `ChatComposerHint`; `ChatMarkdown` renders the answer.
  No second markdown renderer or composer bar is added.
- The thread list renders from `GET /ask/chat/threads`, supports create, rename,
  and delete with a confirmation dialog naming what goes, and opens the newest
  thread on load. It shows the newest 50 as the server returns them and offers no
  pagination, matching D-11; a list of exactly 50 must not imply the member has
  lost anything older, so the control copy says the list is the most recent.
- The controlled mode the Search page uses (`askedQuery` / `answer` / `hits`) keeps
  working unchanged for a single result, and the `indexReady={false}` state is
  unchanged.
- A regenerate control appears on a finished or failed answer and is absent on a
  `no_relevant_sources` answer.
- **Test (`web/src/test/ask-chat-page.test.tsx`)**: with a stubbed `fetch`
  speaking real frames, sending a message shows the answer progressively and the
  send control becomes Stop; pressing Stop leaves the partial text; the thread
  sidebar lists the server's threads and rename/delete call the right methods;
  switching threads mid-stream keeps the reply in its own thread; reload draws
  the stored answer once.

### ASKCHAT-008: Sources from tool results, citations, and the grounding badge

**Description:** As a member, I want to see the passages the answer used, jump to
the one it cites, and know whether it was actually grounded.

**Depends on:** ASKCHAT-007.

**Acceptance criteria:**

- The sources panel is rendered from the message's `tool-search_documents` output
  parts — never by parsing the answer — lists filename, rank, score, and text,
  and shows `truncated` where the model saw a shortened passage.
- Each passage keeps its vote control and calls the existing feedback service
  with `target: "passage"` and the tool's rank; the answer keeps its vote. After
  a reload the votes render from `GET /ask/chat/threads/{id}` and
  `GET /interactions/{id}` as they do today.
- `[n]` markers in the answer render as focusable buttons that select the
  matching passage in the panel; a marker that resolved to nothing is rendered
  as plain text and is never a link.
- The badge follows FR-009 and D-10 exactly: **Grounded** only when grounded
  *and* cited, **Partly grounded** when grounded without a citation, and the
  landed **Not found in the documents** copy with its landed note for `declined`
  and `no_relevant_sources`. It extends `GROUNDING_BADGE` / `groundingNote` in
  `web/src/features/chat/grounding-copy.ts` rather than adding a second badge
  component. Partly grounded is a warning, never an error, and never blocks the
  answer or the vote.
- FR-034's badge half: a `no_relevant_sources` turn renders the badge, its own
  note, no passage cards, and no Regenerate control — the four rendered states
  are distinguishable without reading the answer's prose.
- The streaming region is `aria-live="polite"`, Stop and Regenerate are named
  buttons, the panel is reachable by keyboard, and focus returns to the composer
  after a turn ends.
- **Test (`web/src/test/ask-chat-sources.test.tsx`)**: a frame set with two
  passages and a `[1]` citation renders two passage cards, the badge Grounded, and
  a citation button that focuses the first card; the same answer with no marker
  renders Partly grounded; an empty tool result renders the landed
  "Not found in the documents" badge with no passage cards and no regenerate; a
  vote on a passage calls the feedback
  endpoint with the rank the tool returned; the live region is asserted on the
  streaming element.

### ASKCHAT-009: Import the existing localStorage sessions (Should)

**Description:** As a member, I want the conversations I already had on this
browser to appear in my history after the upgrade, once.

**Depends on:** ASKCHAT-001, ASKCHAT-006.

**Acceptance criteria:**

- `POST /ask/chat/threads/import` accepts a member's legacy sessions
  (`[{id, title, createdAt, updatedAt, turns: [{interactionId, userQuery,
  answer}]}]`), creates one thread per session and one turn per stored turn, and
  links each turn to its existing interaction **only** when that interaction
  exists and belongs to the caller.
- A turn whose interaction is missing, belongs to another member, or has no id is
  skipped and counted in the response; nothing is invented.
- The import is idempotent per session id and per interaction id: a second call
  creates no duplicate thread and no duplicate turn.
- The page runs the import once on first load when the legacy keys exist, then
  deletes `deepfix.chat_sessions.v2:<ownerId>` and
  `deepfix.active_session_id.v2:<ownerId>`, and never runs again.
- **Integration test** with a real database: two members import overlapping
  interaction ids and each keeps only their own; a re-run is a no-op; the legacy
  keys are gone after the page's first load; a session whose interaction was
  deleted imports its thread with the turn skipped and the count reported.

### ASKCHAT-010: Traces, the feedback export, and the served-set guard

**Description:** As the optimizer, I want each turn's tool calls in MLflow and each
follow-up's standalone query in the dev set, so the loop learns from the
conversation without learning from pronouns.

**Depends on:** ASKCHAT-003, ASKCHAT-004.

**Acceptance criteria:**

- Each turn writes one trace in `deepfix_product` tagged `interaction_id`,
  `thread_id`, `turn_id`, `grounding`, with one child span per tool call carrying
  the tool query, the passage count, the max score, and the duration.
- `trace_request` gains nested-span support (or a sibling helper) without
  changing the shape of the traces `ask` and `retrieval` already write, and a
  tracing failure still never fails a turn.
- `build_questions` and `export_feedback` use `ask_chat_turns.retrieval_query`
  when present and `interactions.query` otherwise; a follow-up turn's exported
  question is the standalone tool query, and the raw message stays in `query`.
- The holdout split is unchanged: `is_holdout` still hashes the normalized text
  and still selects 20%.
- **Integration test**: a two-turn conversation whose second turn's tool query is
  standalone exports two questions, the second one equal to the tool query; an
  `ask` turn from the single-turn `POST /ask` with no `retrieval_query` exports its own query; the
  holdout membership of an unchanged query is identical before and after this
  change; the trace for a turn with two tool calls has two child spans and the
  same `interaction_id` tag as its row.

### ASKCHAT-011: Deployed verification (operator)

**Description:** As an operator, I want one runbook that proves the feature on the
real stack, because a local test cannot prove the BFF streams a body, the
provider streams tool calls, or the memory budget holds.

**Depends on:** ASKCHAT-001 … ASKCHAT-010 and a deployment.
**Priority:** Could — an operator session; a cloud agent cannot run it.

**Acceptance criteria:**

- A runbook under `docs/` covers: one streamed answer observed with the network
  panel showing `text/event-stream` and no model id in any frame; Stop closing
  the connection and the turn settling `cancelled`; a follow-up answered; a
  reload mid-answer resuming; a second browser showing the same thread; another
  account's thread id returning 404; the badge transitions for grounded,
  partly grounded, and the two "not found in the documents" notes.
- `docker stats` is recorded before, during, and after four concurrent turns,
  proving no new container and no cap change, and that the API's memory returns
  to its baseline.
- The runbook records one live tool-call turn against `qwen/qwen3.8-flash` and
  whether `tool_choice: "required"` was honoured, closing the R-2 assumption.
- Its result is recorded as a verification note, not as a test.

## What the code does today

Written from the tree at `223a122` on `feat/hermes-optimizer`, 15 November 2026.
Each fact below is cited by file, because the stories assume them.

- **The Ask page is a form shaped like a chat.** `web/src/features/chat/chat-page.tsx`
  holds `sessions` in `useState`, persists them under
  `deepfix.chat_sessions.v2:<ownerId>` / `deepfix.active_session_id.v2:<ownerId>`
  (`chatStorageKeys`), and — since ASK-006 — keeps
  `pendingBySession: Record<sessionId, query>`, so only the asking session shows
  a thinking card and loses its composer. There is still no `AbortController` and
  no Stop; the send control renders a `Loader2` spinner while its session is
  pending. Votes go through `feedbackService.submit({interactionId, target, rank,
  action})`; a `ChatTurn` carries `interactionId` and `answerVote`, and each hit
  carries its own vote. Since ASK-001 the answer card renders
  `GroundingBadge` + `groundingNote` from `grounding-copy.ts` against the
  response's `grounding` field.
- **The client is non-streaming.** `web/src/services/ask-service.ts` calls the
  generated `askQuestion` (`POST /api/ask`) and throws `AskRequestError` on an
  error payload. There is one stream reader in the repo,
  `web/src/services/admin-chat-sse.ts`, and `api-boundary.test.ts` exempts it by
  relative path.
- **The proxy already streams.** `web/src/server.ts::proxyApi` `fetch`es the
  internal API with the body passed through and returns `new Response(upstream.body, …)`,
  and `web/src/api-proxy.ts::upstreamHeaders` deletes `cookie` and
  `authorization` and sets a short-lived bearer from
  `web/src/lib/auth.server.ts::identityToken`. So a streaming route works through
  it today — the admin chat proves it.
- **`POST /ask` is single-turn.** `src/deepfix_rag/api/routes/retrieval.py::ask_question`
  calls `lease.service.answer(query)`, truncates hits with
  `displayed_limit(config, settings.MAX_DISPLAYED_HITS)`, stores one
  `interactions` row and its hits through `_store` → `trace_request` →
  `record_interaction`, and answers `{interaction_id, answer, error, vote,
  grounding, hits}`. When the floor dropped every candidate, `answer` is
  `NO_RELEVANT_SOURCES_ANSWER`, `error` is null, `hits` is `[]`, and the dropped
  rows are still stored. `GET /interactions/{id}` returns 404 for another user's
  row and returns the same `grounding` value.
- **Generation is non-streaming and single-turn.** `QueryService.answer` runs
  `RAGWorkflow._get_response_synthesizer()`, which builds `SimpleSummarize(llm=…,
  streaming=False)`; `_get_llm` builds `CustomLiteLLM` from
  `require_model("LLM_MODEL")`, `settings.LLM_BASE_URL`, `settings.LLM_API_KEY`.
  `CustomLiteLLM.achat` is `asyncio.to_thread(self.chat)`, which is why D-4
  requires a direct `litellm.acompletion` in the tool loop.
- **The index is served through a lease.** `IndexCoordinator.checkout()` pins the
  serving build and `release()` drops it; `displayed_limit` caps what is shown
  with `settings.MAX_DISPLAYED_HITS` (10). `QueryService.retrieve` is the
  retrieval-only entry point and needs no generation model.
- **The persistence model is `interactions` + `interaction_hits`.**
  `src/deepfix_rag/interactions.py` creates `interactions` with
  `kind CHECK(kind IN ('retrieval', 'ask'))` and adds `user_email` by `ALTER
  TABLE` after a `PRAGMA table_info` check; hits are keyed
  `(interaction_id, rank)`. `record_interaction` stores a snapshot, so a later
  re-index does not rewrite history.
- **Feedback is keyed on those rows.** `src/deepfix_rag/feedback.py` records
  events with `target: "passage" | "answer"` and an optional `rank`,
  `current_votes` resolves the latest event per target and rank,
  `FeedbackWorker` mirrors each event to an MLflow assessment
  (`mlflow.log_feedback` / `override_feedback`) against the row's `trace_id`,
  `build_questions` splits dev/holdout by `is_holdout` (20% by hash), and
  `export_feedback` writes `feedback_dev.jsonl` plus `answer_downvotes.jsonl` for
  the optimizer.
- **Tracing is one span per request.** `src/deepfix_rag/tracing.py::trace_request`
  sets the `deepfix_product` experiment, opens one span, sets inputs/outputs,
  tags `interaction_id`, and returns the trace id or `None`; a failure logs and
  never raises.
- **The admin chat is the template.** `src/deepfix_rag/api/routes/admin_chat.py`
  (1064 lines) has `POST /admin/chat/completions` (SSE, AI SDK UI message stream)
  and `GET /admin/chat/turns/{turn_id}/stream`, with disconnect handling that
  marks `cancelled` and keeps partial text; `src/deepfix_rag/api/chat_stream.py`
  builds `start`, `text-*`, `data-deepfix-tool`, `data-deepfix-turn`, `error`,
  and `finish` parts as pure functions; `src/deepfix_rag/api/chat_turns.py` keeps
  the live-turn registry; `src/deepfix_rag/admin_chat.py` owns threads, turns,
  messages, actions, and audit. On the web side,
  `web/src/features/admin-chat/chat-transport.ts` creates one `Chat` per thread,
  `chat-session.tsx` wraps it in `useAISDKRuntime`, and `chat-composer.tsx` uses
  `ComposerPrimitive.Cancel` for Stop. There is **no** `tool-input-*` or
  `tool-output-*` builder yet — the existing `data-deepfix-tool` part is a
  progress *data* part, not a real tool call.
- **The shared surface is two modules.** `web/src/components/chat/markdown.tsx`
  (`ChatMarkdown`) and `web/src/components/chat/composer.tsx`
  (`ChatComposerBar`, `ChatComposerHint`). The Ask page already uses both
  (CHAT-016).
- **The ASK- work has landed.** At `223a122` the tree carries
  `src/deepfix_rag/grounding.py` (`Grounding` with `grounded` / `declined` /
  `no_relevant_sources`, `DEFLECTION_PHRASES`, `DEFLECTION_PATTERNS`,
  `grounding_for`), a required `grounding` field on `AskResponse` and
  `InteractionResponse`, `rerank.min_relevance_score` (0.3) in `RAGConfig` and
  `configs/champion.yaml`, `RAGWorkflow._ranker_split` /
  `split_by_relevance_floor`, `RAGResult.dropped_results`, and
  `pendingBySession: Record<sessionId, query>` in `chat-page.tsx`. The stories
  consume exactly these names.
- **The model advertises tool calling.** OpenRouter's live model list on
  15 November 2026 reports `qwen/qwen3.8-flash` with `tools` and `tool_choice` in
  `supported_parameters` and a 1,000,000-token context. `example.env` already
  ships `LLM_MODEL=qwen/qwen3.8-flash`, `EMBEDDING_MODEL=perplexity/pplx-embed-v1-4b`,
  and `COHERE_MODEL=cohere/rerank-4-fast`.
- **The champion config is fixed.** `configs/champion.yaml`: `hybrid` backend,
  `top_k: 50`, `fusion_mode: reciprocal_rerank`, rerank `enabled: true` with
  `top_n: 5`, `generation.temperature: 0.0`, `max_tokens: 4096`,
  `simple_summarize`, `eval.cutoff_k: 5`, `language: german`.
- **The gate is retrieval-only and `apply_candidate` is the only writer.**
  `src/deepfix_rag/promotion.py::_scoring_config` forces `retrieval_only=True`
  and scores dev and holdout; `apply_candidate` builds beside, scores, and only
  then promotes. No other module writes `configs/champion.yaml`.
- **The host is small.** `docs/deployment.md`: 7.6 GB total, `hermes` 4 GB + 2
  CPUs, `mlflow` 2 GB, `rustfs` and `diagnostic-agent` 1 GB each, and
  `backend-api` / `web` uncapped. `docker-compose.yml` gives `backend-api` an
  explicit environment allowlist with no `env_file`.

## Design Considerations

- **Why one tool and not three.** A `list_documents` or `get_document` tool would
  make the loop able to browse, at the cost of a second retrieval contract and a
  prompt budget that no longer bounds. Search returns whole chunks with their
  filename, which is enough to answer and to show evidence.
- **Why the cutoff lives in the tool.** A prompt instruction ("ignore passages
  below 0.4") cannot be enforced and cannot be reported. Filtering in the tool
  makes `no_relevant_sources` a fact about the retrieval rather than a claim
  about the model.
- **Why the server may run the tool itself.** Requiring retrieval is a product
  promise. Trusting `tool_choice` to deliver it makes the promise a provider
  behaviour; the fallback costs one retrieval and makes the promise structural.
- **Why the sources panel is rebuilt from tool parts.** The model's prose is
  lossy and a parser around it is a second, worse contract. The tool output is
  already structured, already persisted, and already what the model saw.
- **Why the ASK- enum is not widened.** Two agents editing one enum is how a
  branch conflict becomes a data bug. Citations are a separate field with their
  own verdict, and the badge composes the two.
- **Why the `interactions` kind stays `'ask'`.** It is the same row the feedback
  worker, the history screen, and the optimizer already read. A new kind would
  need a table rebuild for a `CHECK` constraint and would hide member chat from
  the admin history it belongs in.
- **Why the history window is messages, not tokens.** A token budget needs a
  tokenizer in the request path; twelve messages bounds the prompt well enough
  for a chat, and `ASK_CHAT_MAX_PASSAGE_CHARS` bounds the other half of it.
- **Why no query-rewriting model call.** The model that answers already has the
  history, and its tool argument *is* a rewrite it wrote for retrieval. A second
  call would add latency, a second prompt to evaluate, and a second place for a
  follow-up to go wrong.
- **What is deliberately shared with the admin chat.** `chat_stream.py`'s part
  builders (extended), `chat_turns.py`'s live-registry shape, the one-`Chat`-
  per-thread transport pattern, `ChatMarkdown`, `ChatComposerBar` /
  `ChatComposerHint`, and the Stop control's `ComposerPrimitive.Cancel` shape.
  What is deliberately **not** shared: the admin chat's action cards, audit
  trail, admin gate, `HERMES_CHAT_*` settings, and its `data-deepfix-tool`
  progress part — Ask has no jobs and no writes, so none of that applies.

## Success Metrics

- A member gets a readable first token without waiting for the whole answer; the
  turn's `first_token_ms` is recorded on the row and in the log line.
- Stop ends an answer within one second of the click, and the stopped turn keeps
  its text and its passages.
- A follow-up question is answered using the earlier turn: the ratio of turns
  whose `retrieval_query` differs from the raw message is observable per thread,
  and a spot check of ten follow-ups reads correctly.
- Every answer's badge matches its data: zero turns labelled Grounded without a
  valid citation, and the partly-grounded rate is readable from
  `ask_chat_turns.citations` so a rise is noticed rather than argued about (D-10).
- Zero 500s from the ask-chat routes in a week of pilot use, and zero `running`
  rows after any restart.
- The optimizer's dev set grows with standalone questions: no exported question
  is a bare pronoun sentence after ASKCHAT-010.
- The deployed stack runs the same containers with the same caps, and four
  concurrent turns leave the API's memory at its baseline afterwards.

## PRD quality gates (self-check)

- [x] **Every Must FR has testable acceptance wording.** Each names a route, a
  part type, a setting, a status code, a stored field, or a visible control, and
  the story that carries it names the integration test that observes it.
- [x] **No duplicate FRs.** FR-006 (hit ranks for votes) and FR-019 (the
  interaction row) are adjacent but distinct: one is the join table's ordering,
  the other is the row that makes votes, history, and export work. FR-008
  (citation validation) and FR-009 (the status enum) are the two halves of D-3
  and are cross-referenced.
- [x] **Non-goals do not contradict a Must FR.** The non-goals exclude
  conversation-level eval and chat-driven writes; no Must FR requires either.
  FR-023 (no promotion path) and FR-024 (eval unchanged) are the enforcement.
- [x] **Terms are glossary-consistent.** Turn, thread, tool, passage, cutoff,
  grounding status, citation, interaction, champion, UI message stream are used
  in exactly the glossary's sense throughout, including in the stories and the
  traceability table.
- [x] **Security and privacy are covered** — NFR-4, NFR-5, FR-013 (injection),
  FR-010 (isolation), FR-001 (no credential in the browser), FR-023 (no holdout
  write), R-5, R-6 — because this feature carries authn, private conversation
  data, member-uploaded content, and model credentials.
- [x] **Every FR maps to at least one goal** (see Traceability) and each FR is
  reachable from a story (see Story to FR map). No FR is marked "New": each is
  traceable to a goal derived from the owner's brief.
- [x] **Every decided question is carried through, not just recorded.** The four
  answers of 2026-11-15 were traced to every place the earlier text asserted the
  opposite: D-7 removed `ASK_CHAT_MODEL` from FR-025, ASKCHAT-003, ASKCHAT-005,
  the release acceptance criteria and the assumptions; D-9 is in FR-010 and
  ASKCHAT-001's delete criteria and test; D-10 is FR-009, ASKCHAT-008 and Success
  Metrics; D-11 is FR-010, ASKCHAT-001's no-prune test and ASKCHAT-007's list
  copy. A search for `ASK_CHAT_MODEL` in this document now returns only the
  record of the decision and the guards that keep it from returning.

## Validation (validate-prd)

Validated with `validate-prd` from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`. The full report is
`tasks/prd-ask-streaming-multiturn-validation.md`; the short version:

- **First pass: Block.** Five blockers, all found by reading the tree rather than
  the draft: (1) `CustomLiteLLM.achat` is `asyncio.to_thread`, so the drafted
  Stop could not cancel the model request while the draft claimed it did;
  (2) the drafted sources panel had no path to `interaction_hits`, which would
  have silently killed passage votes; (3) `interactions.kind` has a `CHECK`
  constraint, so a new kind would need a table rebuild the draft assumed away;
  (4) the drafted grounding enum added a fourth value to the ASK- work's three,
  which is a collision with an in-flight edit; (5) multi-turn history as drafted
  would have fed pronoun-only sentences into `build_questions`, which the
  optimizer scores as retrieval failures.
- **Second pass: Pass with actions.** All five are closed in the text above —
  D-4 and FR-014 name the direct `litellm.acompletion` stream and the closed
  generator; FR-006 requires the tool's passages to be written as hits in rank
  order and keeps their full text so `passage_key` matches; D-5 and FR-019 keep
  `kind='ask'` and add two nullable columns instead; D-3 keeps the three statuses
  and adds a separate validated `citations` field; FR-012 and FR-021 store and
  export `retrieval_query` through a `LEFT JOIN`. Six should-fix items are also
  closed, including the invented `superseded` vote action (now the existing
  `clear`, because `feedback_events.action` has its own `CHECK`) and an
  overstatement about which secret names `_SECRET_ENVS` is missing.
- **Third pass: Pass.** The four questions open on the second pass were answered
  by the product owner on 2026-11-15 and are now **D-7** (no `ASK_CHAT_MODEL`;
  the chat resolves `LLM_MODEL`), **D-9** (a thread delete keeps `interactions`
  and votes), **D-10** (grounded without a citation shows as Partly grounded) and
  **D-11** (threads unbounded, 50-item list cap, no prune). Each was traced to
  every place the earlier text asserted the opposite — FR-009, FR-010, FR-025,
  ASKCHAT-001, 003, 005, 007, 008, the release acceptance criteria, the
  assumptions list and the "Won't" list — and the pass re-checked the dimensions
  against the changed text rather than the earlier draft. No blocker, no
  should-fix item, and no open question remain; the "Open questions" section is
  kept as an index of where each answer lives so a reader who saw an earlier
  draft is not left guessing.
- **Fourth pass: Pass.** Written after ASK-001, ASK-002 and ASK-006 landed
  (`97aef60`, `00aba89`, `223a122`) and after the eleven stories were drafted in
  `tasks/stories-ask-chat/`. Three factual claims had gone stale and are
  corrected: the PRD no longer says the ASK- work is "in flight" or absent from
  the tree; the `Grounding` enum and its classifier are named as landed code
  rather than a forecast; and the tool is described as **reporting** the
  relevance floor rather than applying it a second time, because
  `QueryService.retrieve` already applies it through `_search_split`. D-10's
  badge table is also reconciled with the copy ASK-001 actually ships
  ("Not found in the documents" for both `declined` and `no_relevant_sources`,
  separated by their notes), which leaves this feature adding exactly one
  rendered state rather than four.
- Dimensions at sign-off: Completeness green, Consistency green, Testability
  green, Traceability green, Feasibility **amber** (two provider behaviours are
  asserted from documentation and a model catalogue rather than measured here,
  which is why ASKCHAT-011 exists; this is a property of the plan, not an
  unclosed action), Security green.

## Open questions

**None remain open.** Four were open on the first draft; the product owner
answered all four on **2026-11-15**, and they are now decisions with their
reasoning and their consequences recorded:

| Was open | Answer | Recorded as |
| :--- | :--- | :--- |
| Should an `ASK_CHAT_MODEL` setting exist? | No. The chat resolves `LLM_MODEL` directly; `qwen/qwen3.8-27b` stays only as a documented manual fallback via `LLM_MODEL`. | **D-7** |
| Do a member's votes survive a thread delete? | Yes. The thread and its turns go; the `interactions` rows and votes stay. | **D-9** |
| May an answer be grounded without a citation? | Yes, and it shows as **Partly grounded**. | **D-10** |
| Do threads need a retention window? | No. Unbounded, with a 50-item list cap and no prune. | **D-11** |

Two further questions were answered by the code rather than by anyone's
judgement, and are recorded where they belong rather than here: the model does
advertise tool calling (D-7, and OpenRouter's live catalogue), and the BFF already
streams response bodies (`web/src/server.ts`, and "What the code does today").

Each decision names the condition that would reopen it, so a later reader does
not have to guess whether it was examined or merely assumed:

- **D-7** reopens if a model is needed for the chat that must not serve eval —
  and the answer then is a decision about splitting the scored path from the
  served path, not a new setting.
- **D-9** reopens only as a product decision about dataset retention, which
  belongs to the optimizer's design, not to this feature.
- **D-10** reopens if the partly-grounded rate turns out to be high enough to be
  a defect rather than an outlier.
- **D-11** reopens if the thread tables become a measurable share of
  `product_db_path`.

## Handoff

The next steps are **`validate-prd`** (done; see above) and then
**`create-story`** for ASKCHAT-001 through ASKCHAT-011, one file each under
`tasks/stories-ask-chat/` with a `*-validation.md` sibling, implemented in the
order in "User stories". Those files exist, and this section is kept as the PRD's
own record of the order. The ASK- work that carries the `Grounding` enum and
`rerank.min_relevance_score` is already on the branch at `223a122`, so nothing
blocks ASKCHAT-001.

When a story and this document disagree, the story file is the more specific
statement of intent and this document is the more general one; a disagreement is
a defect in one of them and is fixed in the story file first, because that is
what an implementer reads.

# PRD validation

- PRD path: `tasks/prd-ask-streaming-multiturn.md`
- Validated: 15 November 2026, against the tree at `642f720` on `feat/hermes-optimizer`
- Verdict: **Pass with actions**

Produced with `validate-prd` from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`. The skill's input
path is `docs/prd.md` and its output `docs/prd-validation.md`; this repository
keeps product planning in `tasks/`, so the PRD and this report sit beside
`tasks/prd-admin-hermes-chat.md` and its siblings. Nothing else about the skill's
dimensions, verdict rules, or report template is changed.

## Summary scores

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | All twelve required sections are present: Overview, Goals and non-goals, Personas/actors, Scope (with "Won't"), User journeys, FR-001…FR-034 with priority and rationale, NFR-1…NFR-9, release acceptance criteria, Dependencies & assumptions, eight risks with mitigations, Traceability, and four numbered open questions. Plus a Glossary, a Decisions section (D-1…D-8), "What the code does today", Design Considerations, Success Metrics, and the story list the repository's house style puts in the PRD. |
| Consistency | Green | Glossary nouns (turn, thread, tool, passage, cutoff, grounding status, citation, interaction, champion, UI message stream) are used in one sense throughout, including inside stories. No Must FR contradicts "Out of scope" or "Won't". Two contradictions the first draft carried are closed: the drafted `superseded` vote action against `feedback_events`' `CHECK(action IN ('up','down','clear'))`, and the drafted truncation of stored passage text against `passage_key`. |
| Testability | Green | Every Must FR names an observable: a route and status code, a stream part `type`, a stored column and value, a setting name with a default, or a named control. The story that carries each FR names the integration or end-to-end test that observes it, and no story's evidence is a narrow unit test. |
| Traceability | Green | The Traceability table maps FR ranges to G1…G7; every goal has FRs and every FR appears. The Story to FR map assigns FR-001…FR-034 across ASKCHAT-001…011 with FR-022's whole-set property stated explicitly rather than left as a gap. The open questions are cross-referenced from D-4, D-5, D-7 and D-8. |
| Feasibility | Amber | Every claim about the current tree is verified by file. Two claims cannot be verified on this VM and are labelled: that `qwen/qwen3.8-flash` honours `tool_choice: "required"`/`"none"` through OpenRouter (its `supported_parameters` advertise both, which is evidence, not proof), and that closing LiteLLM's async generator closes the upstream connection. Both are bounded by FR-005's server-side fallback, FR-027's timeout, and ASKCHAT-011's live measurement. |
| Security & privacy | Green | Applicable and addressed. Authentication and per-owner scoping on all six routes (FR-010, NFR-4, R-5); no credential or model id in the browser (FR-001, G7, NFR-4, R-8); prompt injection from member-uploaded corpus text (FR-013, R-6); conversation content and MLflow posture (NFR-5); no holdout read and no promotion path (FR-023, G6). |

## Findings

### Blockers (must fix before story breakdown)

None remain. Five were found on the first pass and each is closed in the PRD's
text; they are listed because a reviewer will otherwise re-raise them.

1. **Stop could not stop the model call, though the draft said it did.**
   `CustomLiteLLM.achat` in `src/deepfix_rag/pipeline.py` is
   `asyncio.to_thread(self.chat, …)`, so cancelling the surrounding task abandons
   the await and leaves the HTTP request to OpenRouter running to `max_tokens`.
   The draft's FR-014 promised cancellation. **Closed** by D-4 and FR-014: the
   tool loop uses `litellm.acompletion(..., stream=True)` directly and closes the
   async generator, and by R-1/R-2 which record the residual cost and its bound.
2. **The sources panel had no route to the passages it must render.**
   `feedback_events` is keyed on `(user_id, interaction_id, target, rank)` and
   `record_vote` resolves a passage vote from `interaction.hits`, so a panel built
   from stream frames alone would have produced votes that no row could resolve.
   **Closed** by FR-006: the tool's passages are written as that turn's
   `interaction_hits` in tool order, so `POST /feedback` works unchanged.
3. **A new `interactions.kind` would have needed a table rebuild.**
   `src/deepfix_rag/interactions.py` declares
   `kind TEXT NOT NULL CHECK(kind IN ('retrieval', 'ask'))`, and SQLite cannot
   alter a `CHECK` in place. **Closed** by D-5 and FR-019: one row per turn with
   `kind='ask'` and two nullable `thread_id`/`turn_id` columns added the way
   `user_email` already was. This also keeps member chat visible to the admin
   history screen and to `export_feedback` with no second code path.
4. **The draft widened an enum another agent is editing.**
   The in-flight ASK- work defines `grounded` / `no_relevant_sources` /
   `declined`; the draft's badge wanted a fourth status. **Closed** by D-3: the
   three statuses are consumed unchanged, and the citation verdict is a separate
   validated field, with the badge composing the two.
5. **Multi-turn history would have poisoned the feedback dataset.**
   `build_questions` groups on `interactions.query` and `is_holdout` hashes it, so
   a follow-up turn would have exported "And who voted against it?" as a dev
   question and scored it as a retrieval miss. **Closed** by FR-012 (the tool's
   own query is stored as `retrieval_query`) and FR-021 (`_latest_events` selects
   `COALESCE(t.retrieval_query, i.query)`).

### Should-fix

Fixed in the PRD; listed because each is a place a later edit can regress.

1. **`superseded` was not a legal vote action.** It collided with the same
   `CHECK`-constraint problem as finding 3, in a second table
   (`feedback_events.action`). FR-015 and D-4 now use the existing `clear`
   action, which `feedback.py` already maps to `mlflow.delete_assessment` — the
   exact intent. No schema change and no new action.
2. **Truncating the passage sent to the model would have split passage
   identity.** `passage_key` is `sha256` of the exact chunk text, and
   `build_questions` groups on it. FR-006 now requires the full text in
   `interaction_hits` and truncation only in what reaches the model, so a chat
   passage and the same chunk's single-turn passage share one key.
3. **A setting added only to `Settings` is invisible on the deployment.**
   `docker-compose.yml` gives `backend-api` an explicit environment allowlist and
   no `env_file`; CHAT-001 hit exactly this. FR-028 and ASKCHAT-005's plumbing
   test require every `ASK_CHAT_*` name in `Settings`, in the Compose environment
   with a non-empty default, and in `example.env`, and R-8 records the failure
   mode.
4. **The stream reader would have broken `api-boundary.test.ts`.** That test
   fails any `fetch(` outside the generated client, `server.ts`, and `api.ts`, and
   its `EXEMPT` set matches on `relative(SRC, file)`. FR-032 names the module and
   the exact join-path form, so a bare filename cannot silently miss.
5. **Redaction was overstated.** The first draft said `_SECRET_ENVS` needed
   `LLM_API_KEY` added; it is already there (`src/deepfix_rag/optimization.py`).
   NFR-4 now names the two names actually missing, `EMBEDDING_API_KEY` and
   `COHERE_API_KEY`.
6. **The ASK- dependency was described as present.** It is not in the tree at
   `642f720`: there is no `grounded`, `no_relevant_sources`, `declined`, or
   minimum-relevance setting anywhere in `src/`, `web/src/`, or `configs/`, and
   `chat-page.tsx` still carries one page-level `pending`. "Dependencies &
   assumptions" now says so, orders ASKCHAT-002 after it lands, and states the
   reconciliation cost if the names differ.

### Nice-to-have

1. Success Metrics mixes hard measurements (`first_token_ms` recorded) with spot
   checks ("ten follow-ups read correctly"). That is deliberate — the first is a
   column, the second is a human judgement no CI can make — but a future revision
   could separate "instrumented" from "reviewed by hand".
2. The story list lives in the PRD rather than in `tasks/stories-ask-chat/`. It
   follows `tasks/prd-admin-hermes-chat.md`, and `create-story` will still produce
   one file per story with a validation sibling. `create-prd`'s own rule ("do not
   add `US-*` stories") is honoured in substance: the ids are `ASKCHAT-*`, and the
   story bodies here are summaries with acceptance criteria, not the deliverable
   story files.

## Suggested edits (applied)

- D-4 and FR-015: replace the invented `superseded` event with the existing
  `clear` action, and say why (the `CHECK` constraint).
- FR-006: split "the text the model saw" from "the text the store keeps", and
  cite `passage_key`.
- FR-021: name the mechanism (`LEFT JOIN ask_chat_turns` on
  `COALESCE(t.retrieval_query, i.query)`) instead of describing an outcome.
- NFR-4: correct which secret names `_SECRET_ENVS` is missing.
- "Dependencies & assumptions": state that the ASK- work is absent from the tree
  and give the reconciliation path.
- Story to FR map: give FR-022 an explicit whole-set note instead of leaving it
  unassigned, and correct ASKCHAT-007/008 so FR-007 (the panel from tool results)
  sits with the panel story rather than the page story.

## Questions for authors

1. `ASK_CHAT_MODEL` (open question 1) — keep the separate setting with a fallback
   to `LLM_MODEL`, or resolve `LLM_MODEL` directly? The PRD recommends keeping it,
   mirroring `HERMES_CHAT_MODEL`.
2. Thread deletion versus votes (open question 2) — keep the `interactions` rows
   and the votes when a thread is deleted, or cascade? The PRD recommends keeping
   them, because they are the optimizer's dataset.
3. Grounded-but-uncited (open question 3) — allow it as **Partly grounded**, or
   treat it as a failure? The PRD recommends allowing and counting it.
4. Thread retention (open question 4) — unbounded with a 50-thread list cap, or a
   retention prune on the existing scheduler? The PRD recommends unbounded for
   this release.

None of the four blocks story breakdown: each recommendation is already
implemented by a named story, and each reversal is one predicate, one settings
line, or one delete cascade.

## Verdict reasoning

`validate-prd` blocks when a Must FR is untestable or when goals and non-goals
contradict. Neither applies: every Must FR names its observation, and the
non-goals (no conversation eval, no chat-driven writes, no attachments, no model
picker) are all consistent with the Must set. Feasibility is the single Amber
because two provider behaviours are asserted from documentation and a model
catalogue rather than measured here, and both are bounded by a fallback in the
product design plus a live check in ASKCHAT-011. That is the correct state for a
plan, and it is why the verdict is **Pass with actions** rather than Pass: the
four open questions are owner decisions with implemented defaults.

Next step: **`create-story`** for ASKCHAT-001, one file under
`tasks/stories-ask-chat/` with a `*-validation.md` sibling. ASKCHAT-002 must not
start until the ASK- minimum-relevance work is on the branch.

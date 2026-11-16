# PRD validation

- PRD path: `tasks/prd-ask-streaming-multiturn.md`
- Validated: 15 November 2026 (fourth pass), against the tree at `223a122` on
  `feat/hermes-optimizer`
- Verdict: **Pass** — no blockers, no open questions, no actions outstanding

Produced with `validate-prd` from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`. The skill's input
path is `docs/prd.md` and its output `docs/prd-validation.md`; this repository
keeps product planning in `tasks/`, so the PRD and this report sit beside
`tasks/prd-admin-hermes-chat.md` and its siblings. Nothing else about the skill's
dimensions, verdict rules, or report template is changed.

Passes: the first validated the original draft (**Block**, five blockers). The
second verified those fixes and the six should-fix items (**Pass with actions**,
the four open questions remaining). The third validated the PRD after the product
owner answered all four on 2026-11-15, and checked that each answer was carried
through every requirement, story, and acceptance criterion rather than only
recorded. The fourth — this one — was written after **ASK-001, ASK-002 and
ASK-006 landed** (`97aef60`, `00aba89`, `223a122`) and after the eleven stories
were drafted in `tasks/stories-ask-chat/`, and it re-checked every claim the PRD
makes about the code against the code that now exists.

## Summary scores

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | All twelve required sections are present: Overview, Goals and non-goals, Personas/actors, Scope (with "Won't"), User journeys, FR-001…FR-034 with priority and rationale, NFR-1…NFR-9, release acceptance criteria, Dependencies & assumptions, eight risks with mitigations, Traceability, and an "Open questions" section that records that none remain and indexes where each answer now lives. Plus a Glossary, a Decisions section (D-1…D-11, dated), "What the code does today", Design Considerations, Success Metrics, and the story list the repository's house style puts in the PRD. |
| Consistency | Green | Glossary nouns (turn, thread, tool, passage, cutoff, grounding status, citation, interaction, champion, UI message stream) are used in one sense throughout. No Must FR contradicts "Out of scope" or "Won't". The four decided answers now agree with every place the earlier text asserted the opposite: a tree-wide search for `ASK_CHAT_MODEL` returns only the decision record and the guards that stop it returning. |
| Testability | Green | Every Must FR names an observable: a route and status code, a stream part `type`, a stored column and value, a setting name with a default, or a named control. The story that carries each FR names the integration or end-to-end test that observes it, and no story's evidence is a narrow unit test. D-7's answer is testable in both directions: ASKCHAT-005 asserts no `ASK_CHAT_.*MODEL` setting exists, and that the loop resolves the `LLM_MODEL` fixture. |
| Traceability | Green | The Traceability table maps FR ranges to G1…G7; every goal has FRs and every FR appears. The Story to FR map assigns FR-001…FR-034 across ASKCHAT-001…011 with FR-022's whole-set property stated explicitly. Each of D-7, D-9, D-10 and D-11 is cited from the FR that carries it and from the story that tests it. |
| Feasibility | Amber | Every claim about the current tree is verified by file. Two claims cannot be verified on this VM and are labelled: that `qwen/qwen3.8-flash` honours `tool_choice: "required"`/`"none"` through OpenRouter (its `supported_parameters` advertise both, which is evidence, not proof), and that closing LiteLLM's async generator closes the upstream connection. Both are bounded by FR-005's server-side fallback, FR-027's timeout, and ASKCHAT-011's live measurement. Amber here is a property of the plan, not an unclosed action. |
| Security & privacy | Green | Applicable and addressed. Authentication and per-owner scoping on all six routes (FR-010, NFR-4, R-5); no credential or model id in the browser (FR-001, G7, NFR-4, R-8); prompt injection from member-uploaded corpus text (FR-013, R-6); conversation content and MLflow posture (NFR-5); no holdout read and no promotion path (FR-023, G6). D-9 is a privacy decision as well as a dataset one: a deleted conversation leaves the admin history screen's feedback record intact, which the PRD now states explicitly so it is a known consequence rather than a surprise. |

## Third pass: the four answers, and what changed for each

The product owner answered on **2026-11-15**. Each answer became a decision with
a date, a statement of what it replaces, and a follow-through check.

| Answer | Decision | Carried through |
|--------|----------|-----------------|
| No `ASK_CHAT_MODEL`; resolve `LLM_MODEL`; `qwen/qwen3.8-27b` only as a manual fallback via `LLM_MODEL` | **D-7**, rewritten around the decision | FR-025 loses the setting; ASKCHAT-003 resolves `require_model("LLM_MODEL")` + `resolve_litellm_model(...)`; ASKCHAT-005 drops the variable from its settings list and gains a test that no `ASK_CHAT_.*MODEL` field or `example.env` variable line exists, plus a test that the loop's model equals the `LLM_MODEL` fixture; FR-028 states the prohibition; release AC 11 says so; "Assumptions" gains a decided entry; the "Won't" list says the chat resolves `LLM_MODEL` and nothing else. The only `example.env` change is one comment naming `qwen/qwen3.8-27b` as the fallback — a comment, so the guard test still passes. |
| Thread delete keeps `interactions` rows and votes | **D-9** | FR-010 already said so and now cites D-9; ASKCHAT-001's delete criterion names `interaction_hits`, `feedback_events`, `feedback_mirrors`, and `feedback_comments`; its test asserts the surviving vote is still returned by `current_votes` and still exported by `build_questions`; D-9 records the consequence that the admin history screen keeps showing those interactions |
| A grounded but uncited answer shows as **Partly grounded** | **D-10** | FR-009 states all four badge states rather than two; ASKCHAT-008 says Partly grounded is a warning, never an error, and never blocks the answer or the vote; Success Metrics makes the partly-grounded rate readable from `ask_chat_turns.citations`; D-10 holds the badge table |
| Threads unbounded, 50-item list cap, no prune | **D-11** | FR-010 says "no pagination beyond it" and "no retention window and no prune job"; ASKCHAT-001 adds a no-prune criterion and a test that a years-old thread still lists and loads; ASKCHAT-007's list criterion says the 50 is a display cap and the control copy must not imply loss; D-11 names the conditions that would reopen it |

## Fourth pass: what the landed ASK- work changed

Three claims the PRD made about the tree were stale, and D-10's badge table
disagreed with the copy that had shipped. All four are corrected in this pass.

| Claim | Was | Now |
| :--- | :--- | :--- |
| The ASK- work's status | "in flight … not in the tree at `642f720`", with a `rg` result quoted and a rename cost stated | Landed at `97aef60` / `00aba89` / `223a122`; `src/deepfix_rag/grounding.py`, `rerank.min_relevance_score`, and `pendingBySession` are named as code the stories consume |
| Who applies the relevance floor | The tool drops passages below the cutoff, using `split_by_relevance_floor` | `QueryService.retrieve` already applies it through `_search_split`; the tool **reports** survivors and does not filter again. ASKCHAT-002's AC 4, 5 and 11 are rewritten accordingly — the first draft asserted the tool would return `[]` while `retrieve` returned passages, which is the divergence FR-024 forbids |
| The badge's rendered states | Four states with four labels, two of them new | ASK-001 ships "Not found in the documents" for both `declined` and `no_relevant_sources`, separated by their notes, so this feature adds **one** rendered state (**Partly grounded**). `GROUNDING_BADGE` is a `Record<Grounding, …>`, so it must be a `(grounding, citationCount)` function rather than a fourth key |
| The tool's `top_k` | "integer 1..8, default `rerank.top_n`" | Clamped into `[1, displayed_limit(config, MAX_DISPLAYED_HITS)]`, the helper the product already uses to decide what a member is shown |

## Findings

### Blockers (must fix before story breakdown)

None. The five found on the first pass remain closed and are recorded here
because a reviewer reading only the final text would otherwise re-raise them:
`CustomLiteLLM.achat` being `asyncio.to_thread` (D-4, FR-014); the sources panel's
missing route to `interaction_hits` (FR-006); a new `interactions.kind` needing a
table rebuild because of the `CHECK` (D-5, FR-019); widening the in-flight ASK-
grounding enum (D-3); and multi-turn history poisoning `build_questions` (FR-012,
FR-021).

### Should-fix

None outstanding. The six from the second pass are closed in the PRD: the
`superseded` vote action (now `clear`, the `CHECK`-legal value), truncating stored
passage text (FR-006 keeps the full text so `passage_key` matches), the
`Settings`-without-Compose gap (FR-028, R-8), the `api-boundary.test.ts` exemption
path (FR-032), the overstated redaction claim (NFR-4 now names
`EMBEDDING_API_KEY` and `COHERE_API_KEY`), and the ASK- dependency being described
as present when it is not.

The fourth pass added one of its own and closed it: **the PRD's story bodies and
the story files could have drifted**, because both state acceptance criteria. The
rule recorded in the Handoff is that the story file wins and a disagreement is
fixed there first, and this pass reconciled the two where they touched —
ASKCHAT-002's floor handling, ASKCHAT-008's badge labels.

### Nice-to-have

1. Success Metrics mixes hard measurements (`first_token_ms` recorded) with spot
   checks ("ten follow-ups read correctly"). That is deliberate — the first is a
   column, the second is a human judgement no CI can make — but a future revision
   could separate "instrumented" from "reviewed by hand".
2. The story list lives in the PRD rather than in `tasks/stories-ask-chat/`. It
   follows `tasks/prd-admin-hermes-chat.md`, and `create-story` will still produce
   one file per story with a validation sibling.

## Suggested edits (applied in this pass)

- D-7: rewritten to open with the decision rather than the recommendation, and to
  keep the rejected `ASK_CHAT_MODEL` proposal as the alternative it replaces.
- D-9, D-10, D-11: added, each dated, each naming the open question it answers,
  and each naming the condition that would reopen it.
- "Decisions" preamble: now says which decisions were settled while writing and
  which were answered by the owner on 2026-11-15.
- FR-009, FR-010, FR-025, FR-028: cite the new decisions and state the four badge
  states, the no-prune rule, and the no-model-setting rule.
- ASKCHAT-001, 003, 005, 007, 008: acceptance criteria and tests updated per the
  table above.
- Release acceptance criteria 11 and the "Assumptions" list: state the decided
  answers instead of leaving them implied.
- "Open questions": reduced to an index of where each answer now lives, plus the
  reopening conditions. It is kept rather than deleted so a reader who saw an
  earlier draft is not left guessing.
- "PRD quality gates": one added line asserting that each decided answer was
  traced to every place the earlier text contradicted it.

## Questions for authors

None. All four questions from the second pass are answered and recorded as
decisions; no question in this document is open.

## Verdict reasoning

`validate-prd` blocks when a Must FR is untestable or when goals and non-goals
contradict, and passes with actions when NFR rationale is missing, risks are weak,
or traceability has gaps. None of those applies: every Must FR names its
observation, the non-goals are consistent with the Must set, all nine NFRs carry
rationale, eight risks have mitigations with a named test each, and all 34 FRs are
traced to goals and (save FR-022's stated whole-set property) to stories.

The single Amber — Feasibility — is not an action the author owes. It is two
provider behaviours that can only be settled against a live deployment, which is
what ASKCHAT-011 exists to do, and both are bounded by a design fallback in the
meantime. So this pass is **Pass**: no blockers, no open questions, and only the
two nice-to-haves above, neither of which affects story breakdown.

Next step: **`create-story`** for ASKCHAT-001, one file under
`tasks/stories-ask-chat/` with a `*-validation.md` sibling. ASKCHAT-002 must not
start until the ASK- minimum-relevance work is on the branch.


## Fifth pass: after implementation (2026-11-15)

Validated again after ASKCHAT-001 … ASKCHAT-010 were implemented and committed on
`feat/ask-chat-streaming`, and after a defect found on the deployed stack was
fixed (`3b5d24f`). This pass exists because the fourth validated a *plan*: it
compared the PRD against the tree the plan was written for, not against the code
the plan produced. Fifteen places had drifted, and two behaviours the fourth pass
inherited as "current" were wrong for a product and are now settled.

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | D-4a added (a provider that refuses the forced tool step, with the measurement that settled it); "Corrections after implementation" added as the index of every plan/code divergence; "Open questions" now records the two inherited behaviours as settled rather than open. |
| Consistency | Green | D-5's column list matches the schema including `legacy_session_id` and names the marker table the idempotent delete needs; FR-002's grammar matches what `ask_chat_turn.run_turn` emits; FR-010 states the delete and rename rules the code enforces. |
| Testability | Green | FR-005 now names the exact wire shape (`reasoning` disabled on the forced step, `num_retries=0`) and the two recoveries; FR-016 names the cutoff and the absent verdict; FR-018 and FR-020 name the export exclusion and the `ERROR` trace state. Each is observed by a test that exists. |
| Traceability | Green | D-4a is cited from FR-005, R-2 and ASKCHAT-003; FR-010's delete and rename rules are cited from ASKCHAT-001; no FR lost a goal link. |
| Feasibility | Green | The Amber of the fourth pass is resolved by measurement rather than by argument: the provider's behaviour is now known and worked around, and the workaround is tested against a socket. The one remaining unmeasured item is whether a stopped turn's spend stops upstream, which ASKCHAT-011's runbook still asks an operator to record. |
| Security & privacy | Green | FR-010's 404 for a thread that is not the member's is unchanged, so the idempotent delete does not become a probe; a failed turn's answerless interaction is excluded from the export, so a failure cannot create a dev question. |

### Blockers

None. The deployed defect this pass documents — every turn failing at step 0 —
was a code defect, is fixed in `3b5d24f`, and is now a risk that **occurred** (R-2)
with its mitigation stated as the code that shipped.

### Should-fix (closed in this pass)

1. **The PRD described a plan, and fifteen details had drifted from the code.**
   The "Corrections after implementation" table lists all fifteen with where each
   is now recorded; the story files were corrected in the same commit series.
2. **Two inherited behaviours were documented as open decisions when both had a
   clearly better answer.** A retried delete returning 404 reports a failure for a
   request that succeeded; a rename bound measured on the raw string refuses a valid
   120-character name that arrived with whitespace. Both are now settled, implemented,
   and tested — a 204 for the owner's already-deleted thread with a marker row
   distinguishing it from "never yours", and a bound measured after trimming in both
   the route and the store.
3. **R-2 asserted a provider behaviour that turned out to be false.** It now records
   the measurement and the fix rather than the assumption.

### Nice-to-have

1. The delete-marker table (`ask_chat_deleted_threads`) is a small piece of state
   the plan did not anticipate. It is the minimum that makes "already deleted by
   this member" distinguishable from "never this member's" after the row is gone;
   an alternative is an `owners` tombstone keyed differently, which would be the
   same idea with a worse name.
2. `docs/ask-chat-verification.md` is still `NOT RUN`. Its step 8 now also has a
   reason to be run: it is where the provider's live `tool_choice` behaviour is
   confirmed rather than assumed.

## Verdict

**Pass.** No blocker, no open question, and the three should-fix items are closed
in this pass. Feasibility moves to Green because the one behaviour that was
asserted from documentation is now measured and handled in code.

## Sixth pass: `tool_choice` becomes auto (2026-11-16)

Validated after the product owner replaced the forced-tool rule (**D-4b**) and after
a second live defect was found in the same area (a tool call streamed as many
deltas). Both were confirmed on the deployed stack, not in review.

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | D-4b added with the four-cell verdict table and the reason the rule changed; D-4a marked partly superseded; the correction table grows to seventeen rows; FR-005 and FR-009 rewritten around "the model decides, the server never claims". |
| Consistency | Green | D-3 no longer says the enum is never widened — it says the one widening, `not_searched`, and why the three landed values cannot express it. FR-016's "a turn that never searched carries none" became `not_searched`, because carrying none is no longer a state the product has. |
| Testability | Green | FR-005 names `auto`/`none` and the recovery; FR-009 names the four-cell rule and the badge copy; FR-002 and FR-005 require one assembled `tool-input-available` per call. Each has a test, including the fragmented-delta fixture that the suite previously lacked. |
| Traceability | Green | D-4b is cited from FR-005, FR-009, R-2 and ASKCHAT-003/008; no FR lost a goal link. |
| Feasibility | Green | The measured provider behaviour is unchanged; what changed is that the product no longer sends the request shape that provider refuses. |
| Security & privacy | Green | Unaffected: no new route, no new credential, no change to per-owner scoping. |

### Blockers

One was found again, and it is the more interesting of the two: **the test suite
could not have caught the fragmented-delta bug**, because every fixture sent a whole
tool call in a single chunk. The fix is not only in the loop but in the fixture —
AC 19(e) of ASKCHAT-003 now reproduces the provider's real stream shape.

### Should-fix (applied)

1. D-4a's first bullet (thinking disabled on the forced step) is now dead. Rather
   than deleting the decision, it is marked superseded by D-4b and the unused flag
   was removed from the code, so a reader can see what was tried and why it went.
2. `not_searched` is a widening of a landed enum, which the PRD previously forbade
   outright. D-3 now states the exception and its cost (a guarded one-time rebuild
   of the `grounding` CHECK) instead of quietly contradicting itself.

### Nice-to-have

1. The `not_searched` rate is a useful prompt-quality signal and is recorded as a
   Success Metric; a rise on real questions means the prompt needs work, not that
   the rule is wrong.

## Verdict

**Pass.** No blocker, no open question; the two should-fix items are closed in this
pass.

## Seventh pass: three live findings (2026-11-16)

Validated after `c755c67` ran on the deployment end to end — seven of seven real
questions searched and grounded, traces and spans correct — and live testing found
three defects in the edges around that success.

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | FR-027 now defines the cap as individual searches with its refuse path; FR-009 states the order the verdict is decided in; FR-014 states what a cancelled turn keeps. Correction table to twenty rows. |
| Consistency | Green | The three FRs now agree with D-4b: the model decides *whether* to search, the cap decides *how many*, and the verdict is computed in the order of the facts. No FR claims a step-level cap. |
| Testability | Green | Each fix names its observation, and each has an integration test that was mutation-checked: removing the per-call check lets six searches run, restoring the old verdict order labels a thanks `no_relevant_sources`, and dropping the live wiring loses the cancelled turn's query. |
| Traceability | Green | No FR changed owners; the three fixes stay inside ASKCHAT-003, 004 and 008. |
| Feasibility | Green | Unchanged. |
| Security & privacy | Green | Unchanged. The cap being enforced is itself a denial-of-service bound, which is why it belongs in NFR-3's envelope rather than only in D-8's numbers. |

### Blockers

All three were found on a deployment, not in review, and two of the three were
edge cases around correct behaviour rather than failures: the turn answered, the
badge was simply wrong about *why*. That is the finding worth carrying forward —
the suite tested that a search happens and that an answer is grounded, but not that
the verdict explains itself correctly in every path.

### Should-fix (applied)

1. The cap's unit was never stated. "At most `ASK_CHAT_MAX_TOOL_CALLS` calls per
   turn" reads as a per-step budget when a model can issue several calls in one
   step; FR-027 now says *searches across the turn* and names the refuse path.
2. A cancelled turn's verdict is now computed from the evidence rather than from
   the unfinished answer, so "stopped mid-sentence" cannot read as "the search found
   nothing".

## Verdict

**Pass.** No blocker, no open question.

## Eighth pass: a browser check on the deployment (2026-11-16)

Validated after an operator checked `/chat` in a browser on `333870c`. The
server-side checks all passed — seven of seven questions searched, grounded, and
traced correctly — and the browser found six UI defects around that success.

| Dimension | Status | Notes |
|-----------|--------|-------|
| Completeness | Green | D-10's badge table grows a third row for a reuse answer; FR-009 states it; FR-033 accepts the payload the retired page actually wrote; the correction table reaches twenty-six rows. |
| Consistency | Green | The badge's six rendered states now each describe a distinct fact, and none of them borrows another's label. FR-014's "keeps the text" now has a client half: the page rebuilds from the store after a stop. |
| Testability | Green | Each fix has a test that fails without it, mutation-checked: the title row, the older answer's citation control, the numeric-timestamp import, and the empty text group. |
| Traceability | Green | No FR changed owner. |
| Feasibility | Green | Unchanged. |
| Security & privacy | Green | Unchanged; the import still checks ownership. |

### Blockers

None in the server. All six were client-side or documentation, and the pattern is
worth recording: **the suite proved that the server did the right thing and never
asked what the page showed**. A stopped turn's answer was stored correctly and
disappeared from the screen; a grounded reuse answer was stored correctly and was
badged as a defect; a citation was validated correctly and rendered as a glyph.

### Should-fix (applied)

1. **The badge's "Partly grounded" carried two meanings.** It now covers only the
   searched case, and a reuse answer gets **Reused earlier evidence**. The
   discriminator needed no new server field.
2. **The runbook named an API path as a page.** `/admin/interactions` is the History
   page's API; `/history` is the page. Corrected in the runbook, which is the only
   document that named it as something to click.
3. **The import's timestamp type was narrower than the payload it exists to
   read.** It is the clearest of the six: the feature could not have worked from a
   browser, and the failure was reported as a sentence with no cause.

## Verdict

**Pass.** No blocker, no open question.

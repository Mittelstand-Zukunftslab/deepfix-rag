# PRD validation

- PRD path: `tasks/prd-ask-streaming-multiturn.md`
- Validated: 15 November 2026 (third pass), against the tree at `642f720` on
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
the four open questions remaining). The third — this one — validates the PRD
after the product owner answered all four on 2026-11-15, and checks that each
answer was carried through every requirement, story, and acceptance criterion
rather than only recorded.

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


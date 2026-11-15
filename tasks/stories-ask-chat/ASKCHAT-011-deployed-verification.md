# ASKCHAT-011 — Prove it on the deployed stack

## Type
Chore — an **operator runbook**, not a code story. Its deliverable is a document an operator fills in, and no code change belongs to it.

## Status
Runbook prepared, not run — `ea90987`. `docs/ask-chat-verification.md` is written in full with every result reading `NOT RUN`; an operator executes it and records the observed values.

## Summary
A local test cannot prove that the BFF streams a body unchanged, that the provider honours `tool_choice` on the pinned model, or that four concurrent turns leave the 7.6 GB host where it was. This story is one operator session on the real stack, recorded as a runbook with its result.

## PRD traceability
- Related: all FRs, verified end to end; FR-022 (no regression on the shipped surfaces) is verified here and nowhere else
- Decisions: D-1 (the BFF path), D-7 (`LLM_MODEL` on the real provider), D-8 (the memory envelope), D-10 (the four badge states as rendered)
- Priority: Could. An operator session; a cloud agent cannot run it.
- NFR touchpoints: NFR-3 (no new container, no cap moved), NFR-4 (no credential in any payload), NFR-5 (no holdout content anywhere)

## Dependencies
- Requires: ASKCHAT-001 … ASKCHAT-010, and a deployment of `feat/hermes-optimizer`.
- Blocks: nothing. It is the last story and the only one that is not code.

## Acceptance criteria

**The runbook**

1. Given `docs/ask-chat-verification.md`, when it is read, then it exists and names the deployment it was run against, the date, the operator, and the commit tested.
2. Given the runbook's steps, when they are followed, then each one records a command or an action and the observed result, not an intention. A step with no recorded output is not done.

**What it proves**

3. Given a streamed answer, when the browser's network panel is inspected, then the response is `text/event-stream` with `x-vercel-ai-ui-message-stream: v1`, the frames arrive progressively rather than in one burst, and no frame contains a model id, an API key, or an S3 secret.
4. Given a turn running, when **Stop** is pressed, then the connection closes, the turn settles `cancelled` in the database, the partial text stays on screen, and the sources panel still shows what had been retrieved.
5. Given a follow-up question, when it is asked, then the trace shows a tool call whose query is standalone rather than a pronoun sentence, and the exported dev question for that turn is that query.
6. Given a reload mid-answer, when the page reopens, then the answer resumes or shows its stored text exactly once, with no duplicated paragraph.
7. Given the same account in a second browser, when the thread list loads, then the conversation is there; given a second account, when it requests that thread id, then it gets 404.
8. Given four answers about the same question, when each renders, then the four badge states of D-10 are each observed at least once: **Grounded** with citations, **Partly grounded** with none, and both "Not found in the documents" notes — the declined one and the no-passages one.
9. Given `docker stats` before, during, and after four concurrent turns, when the numbers are recorded, then no new container exists, no `mem_limit` or `cpus` value has changed, and the API's memory returns to its pre-test level afterwards.

**What it settles**

10. Given one live tool-call turn against `LLM_MODEL` (`qwen/qwen3.8-flash`), when it is recorded, then the runbook says whether `tool_choice: "required"` was honoured — that is, whether the model called the tool on the first step — closing the R-2 assumption one way or the other. A "no" is a finding, not a failure: FR-005's server-side fallback means the turn still retrieved.
11. Given a stopped turn, when the provider's usage for that turn is compared with a completed one, then the runbook records whether the stopped turn's spend stopped with it, which is the residual cost R-1 and R-2 describe.
12. Given the shipped surfaces, when the Search page, the admin Hermes chat, the Optimization screen, and the single-turn `POST /ask` are exercised, then each behaves as it did before this feature, and the runbook says so explicitly rather than leaving it unchecked.

**How it is recorded**

13. Given the story's result, when it is written, then it is a verification note under `docs/`, not an automated test — the environment is a deployment, not CI. Anything the note could become a test for is named as a follow-up rather than claimed as covered.

## Out of scope
- Automating the checks in CI. A deployed browser cannot be a build step.
- Load or soak testing beyond four concurrent turns. D-8's numbers are a bound, not a capacity plan.
- Changing any setting as part of verification. If a step needs a different value, the note says which and why, and the change is its own story.
- Verifying the optimizer's next round. That run is its own operation.

## Notes
- This is the only place FR-022 is verified, which is why the story exists rather than being folded into ASKCHAT-010. Every other story's definition of done runs the unchanged suites; none of them can prove the deployed surfaces still work.
- The four badge states are the one thing that is easy to fake locally and hard to see on a deployment, because they depend on real rerank scores. Recording which real question produced each state is the evidence.
- The model id must not appear in any frame. `src/deepfix_rag/api/chat_stream.py` builds every part rather than forwarding upstream objects, which is why this holds structurally; the runbook confirms it on the wire rather than trusting the design.
- One uvicorn worker is assumed throughout. If the deployment is found to run more, the note records it, because resume degrades to stored-text replay in that case and the operator should know.

## Definition of done
- [ ] `docs/ask-chat-verification.md` written and committed with the observed results
- [ ] Every AC above records an output, or states plainly that it could not be run and why
- [ ] `docs/README.md` lists the new document
- [ ] The four badge states and the resource numbers are recorded as evidence
- [ ] No code change in this story, unless a defect it finds needs one — in which case that change is its own story

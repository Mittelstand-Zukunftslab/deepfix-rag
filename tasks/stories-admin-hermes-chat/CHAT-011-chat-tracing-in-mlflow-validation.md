# Story validation: CHAT-011 — Chat tracing in MLflow

- Story file: tasks/stories-admin-hermes-chat/CHAT-011-chat-tracing-in-mlflow.md
- Validated: 2026-11-14
- Verdict: **Pass with actions**

## Findings

### Blockers

1. **The prompt sentence was a two-component contract with no guard.** The plugin parses
   `The DeepFix chat thread id is <thread_id> and the turn id is <turn_id>.` from the prompt that
   `admin_chat.system_prompt` writes, but the plugin runs in the Hermes virtualenv and cannot import the package. A
   silent edit on either side would produce traces with no ids and no failing test.
   *Fixed:* the note requires either one shared constant (where importable) or a test that asserts the literal
   appears in both files, and lists that test in AC 12's coverage. This is the story's most likely silent break.

### Should-fix

2. **The optimizer's plugin behaviour had to be protected explicitly.** AC 8 and AC 9 state that `deepfix.run_id`
   and the scrub are unchanged, and AC 12 requires `tests/test_hermes_run_tag.py` to pass unmodified — otherwise the
   new chat branch could regress the shipped optimization trace.
3. **The `ensure_hermes_trace_experiment` signature change was one-sided.** AC 4 now requires the existing optimizer
   call to keep its default so the current test stays green, which is what makes the parameter addition safe.
4. **Chat-disabled boots would otherwise load plugins.** AC 5 pins that the off path loads nothing and creates no
   experiment, keeping CHAT-001's "no memory when off" honest.

### Nice-to-have
- The two `tasks/prd-hermes-optimizer.md` amendments this story owes are named individually, so the PRD-of-record edit
  is not forgotten and not over-applied (the story explicitly forbids reopening US-001…US-010).
- The MLflow-down behaviour (log one line, do not stop the container) matches the existing optimizer path.

## Suggested edits (concrete)
- None outstanding.

## PRD alignment
- FRs: FR-024.
- Decisions: D-5 — a separate experiment, with a test that the two names differ.
- Gaps: none. Content capture staying off (NFR-5) is AC 11, and no key in a span is AC 9 and AC 12.
- Note for a reader: this story deliberately reverses `tasks/prd-hermes-optimizer.md` US-007's "the chat profile's
  config, `.env`, and home do not get the plugin or `hermes_otel.yaml`". The PRD's amendment list item 3 authorises
  exactly that reversal, and it is written here rather than as a separate docs story.

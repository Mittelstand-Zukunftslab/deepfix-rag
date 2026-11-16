# Story validation: ASKCHAT-007 — Make the Ask page a streaming, stoppable conversation

- Story file: `tasks/stories-ask-chat/ASKCHAT-007-ask-page-streaming-chat.md`
- Validated: 2026-11-15, against the tree at `223a122` on `feat/hermes-optimizer`
- Verdict: **Pass**

## Findings

### Blockers

None. One outcome — the page behaves like a conversation — with every AC naming a control, a route, a rendered state, or a test's observation.

### Should-fix

1. **AC 2 was the story's most at-risk requirement and needed to be explicit about ASK-006.** The landed page keys pending by session (`pendingBySession`); a rewrite onto `useAISDKRuntime` can easily collapse that back into one flag. The AC now states that per-session pending is preserved and that the runtime's `isRunning` follows the *active* thread, which is the only way both properties hold at once.
2. **AC 15 allowed too vague a change to existing tests.** "Changes only where they drove a control this story replaces" is close to untestable. It now names the two things that may change — the send button's behaviour and the page-level pending state — which are exactly the surfaces this story replaces.
3. **AC 10 needed to say what the copy must not claim.** A 50-item list with no pagination reads as data loss unless it is labelled. The AC now says the copy describes the list as the most recent, which is D-11's actual rule.

### Nice-to-have

- The story could specify the empty state's starter prompts. It does not: CHAT-019 and the current page already have them, and this story's job is the transport, not the welcome copy.

## Suggested edits (applied)

- AC 2, AC 10, AC 15 rewritten as above.

## PRD alignment

- FRs: FR-018 (the regenerate control's presence and absence), FR-029 (the page half).
- Decisions: D-1 (the streaming client), D-4 (Stop and regenerate as controls), D-11 (the 50-item cap is a display cap, and the copy must not imply loss), D-9 (the delete dialog names what goes and says votes are kept).
- Gaps: none. FR-030 (the sources panel and its a11y) is ASKCHAT-008; FR-032 (the one reader) is ASKCHAT-006.

## NFR touchpoints

- **NFR-6** — AC 4, AC 12, and the required test: Stop is a named control, the thread list is keyboard reachable, and the streaming state does not trap focus.
- **NFR-7** — AC 1, AC 3: `useAISDKRuntime`, `ChatMarkdown`, `ChatComposerBar`; no second renderer, composer, or runtime.
- **NFR-4** — the page calls only the service; it names no upstream origin.

## Post-implementation validation (2026-11-15)

- Validated: after the story shipped (`d10c656`)
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 1 and AC 13 both described `chat-page.tsx` | AC 13 is `ask-result.tsx`, a separate component | The conversation page runs on `useAISDKRuntime`; the Search hand-off has no runtime and never sends anything. One file could not be both without a runtime with nothing behind it |
| 2 | The page tests in `pages.test.tsx` drove the retired localStorage page | Those describes were removed; `ask-chat-page.test.tsx` covers the conversation, and the controlled-result cases moved with `AskResultView` | The localStorage session model no longer exists |
| 3 | AC 14's index-preparing copy was unspecified beyond "the existing state" | The page renders `IndexPreparing`, the shared component, so the sentence is the same as the Search page's | A second copy of that sentence is the drift FR-031 forbids |

### Blockers

None.

### Should-fix (applied)

1. **The page had to keep the result view's votes working.** `AskResultView` owns
   its own vote state and submits through `feedbackService`, so the Search hand-off
   did not regress when the conversation page took over `chat-page.tsx`.

## Post-implementation validation, second pass (2026-11-16)

- Validated: after a browser check on the deployed stack found three page bugs
- Verdict: **Pass with actions, all applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | AC 7 said the partial answer "stays visible" without saying how | AC 7 requires the page to re-read the thread after a turn and rebuild the answer from the store | An aborted stream leaves the answer's text part unfinished, and the runtime declines to draw an unfinished part — so the answer the server had already stored vanished along with its sources |
| 2 | Nothing said a stored answer's markers are controls | AC 7a requires each assistant message to use the citations **its own turn** recorded | The page handed every message the newest verdict's citations, so an older answer's markers matched nothing and rendered as raw characters |
| 3 | Nothing said where a thread's title comes from | AC 7b requires the list to take the title the server derived from the first question, on open and after a turn | The client kept the empty title it created the thread with, so every row said "Untitled conversation" until renamed by hand |

### Blockers

All three were visible on the deployed page and none of them failed a test, because
no test asserted the *content* surviving a stop, an older answer's citation
controls, or a row's title.

### Should-fix (applied)

1. The stop test was rewritten to reproduce the bug rather than surround it. Its
   first version let the stream close after Stop, so the answer arrived through the
   normal finish path and the test passed with the fix reverted. It now serves a
   stream that keeps the text part open — what a provider looks like while it is
   still writing — and the stored answer is deliberately different text from the
   streamed one, so the assertion can only pass if the text came back from the
   store. With the fix reverted, that test fails.

## Post-implementation validation, third pass (2026-11-16)

- Validated: after a browser check at about 630px and a reload mid-answer
- Verdict: **Pass with actions, applied**

| # | Was | Now | Why |
| :--- | :--- | :--- | :--- |
| 1 | Nothing said how the sources panel behaves below its side column | AC 7c requires it to stack under the conversation, full width, with the composer still reachable | Below `lg` the panel was a `shrink-0` sibling inside a fixed-height column, so it squeezed the chat column toward nothing and sat over the composer |
| 2 | AC 7b said the list takes the derived title but nothing about a running turn | AC 7d requires the badge to describe the newest turn, and to be absent when that turn has no verdict | The page took the last verdict any turn had, so a running turn inherited the previous answer's badge — "Reused earlier evidence" over a turn that had searched |
| 3 | Nothing said the panel must not scroll sideways | AC 7c requires one width from wrapper to panel, a shrinking filename, and the score always in view | The wrapper capped the panel at `max-w-sm` while the panel asked for `26rem` and refused to shrink, so the cards clipped their score and the panel grew a horizontal scrollbar |

### Blockers

Two, both visible in an ordinary visit: the panel covered the composer at a narrow
width, and a reload mid-answer showed a badge for an answer that was not on screen.

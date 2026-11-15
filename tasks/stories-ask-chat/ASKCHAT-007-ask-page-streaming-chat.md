# ASKCHAT-007 — Make the Ask page a streaming, stoppable conversation

## Type
Feature

## Status
Implemented — `d10c656` on `feat/ask-chat-streaming`

## Summary
The page reads like a conversation. Answers arrive as they are written, the send control becomes **Stop** while a turn runs, the thread list comes from the server and can be named and deleted, and a reply in flight stays in the thread that started it.

## PRD traceability
- Related: FR-018 (the regenerate control), FR-029 (the page half)
- Decisions: D-1 (the API streams the AI SDK protocol), D-4 (Stop and regenerate), D-11 (the 50-item list cap is a display cap)
- Journeys: J1, J3
- NFR touchpoints: NFR-6 (keyboard and screen reader reach Stop), NFR-7 (no second renderer or composer), NFR-4 (the page holds no credential)

## Dependencies
- Requires: ASKCHAT-006 (the transport and the data layer).
- Blocks: ASKCHAT-008 (the panel renders inside this page).

## Acceptance criteria

**The runtime**

1. Given `web/src/features/chat/chat-page.tsx`, when it is read, then it runs on `useAISDKRuntime` with one `Chat` per thread from `ask-chat-transport.ts`, keeps a per-thread session map, and no longer calls `askService.ask` to ask a new question.
2. Given the page's pending state, when it is read, then it is still per session, as ASK-006 left it — a turn in one thread does not show a thinking card in another, and does not disable another thread's composer. The runtime's `isRunning` follows the active thread.
3. Given the page's components, when they are read, then the answer renders through `ChatMarkdown` and the composer bar through `ChatComposerBar` / `ChatComposerHint`, and neither `react-markdown` nor a second composer bar is introduced.

**Stop and regenerate**

4. Given a turn running in the active thread, when the composer renders, then the send control is a **Stop** control built with `ComposerPrimitive.Cancel`, named for a screen reader, and pressing it ends the turn.
5. Given a finished or failed turn, when its actions render, then **Regenerate** is offered and calls the transport's reload trigger.
6. Given a turn whose grounding is `no_relevant_sources`, when its actions render, then **Regenerate** is absent. The refusal is the answer.
7. Given a stopped turn, when the page re-renders, then the partial answer stays visible and the composer is usable again.

**The thread list**

8. Given the page loads, when it renders, then the thread list comes from `GET /ask/chat/threads` and the newest thread is open.
9. Given the list, when the member acts on it, then create, rename, and delete each call the corresponding route; delete opens a confirmation dialog that names what goes — the conversation and its messages — and says the votes it recorded are kept.
10. Given a member with more than 50 threads, when the list renders, then it shows the newest 50 as the server returned them, offers no pagination, and its copy says the list is the most recent rather than implying the older threads are gone (D-11).
11. Given a member with no threads, when the page renders, then it shows the empty state and can start a conversation.
12. Given the member switches thread while an answer is still streaming, when they switch back, then the answer is still arriving in the thread it started in and has not appeared anywhere else.

**What must not change**

13. Given the controlled mode the Search page uses (`askedQuery` / `answer` / `hits`), when it renders, then it still shows that single result exactly as before — as **`web/src/features/chat/ask-result.tsx`**, a separate component. The conversation page runs on `useAISDKRuntime` and has no runtime in that mode, so keeping both in one file would have meant either a runtime with nothing behind it or a page that could not be a conversation. AC 1's "the page runs on `useAISDKRuntime`" and this AC are both satisfied by splitting them.
14. Given `indexReady={false}`, when the page renders, then it still shows the existing "index is being prepared" state.
15. Given the existing Ask tests in `web/src/test/pages.test.tsx`, when they run, then they pass, with changes only where they drove a control this story replaces (the send button's behaviour, the page-level pending state).

**The required test**

16. Given `web/src/test/ask-chat-page.test.tsx` with a stubbed `fetch` speaking real frames, when it runs, then: sending a message shows the answer progressively and the send control becomes Stop; pressing Stop leaves the partial text and re-enables the composer; the sidebar lists the server's threads and rename/delete call the right routes; switching threads mid-stream keeps the reply in its own thread; and a reload draws the stored answer once.

## Out of scope
- The sources panel, citation links, and the grounding badge. ASKCHAT-008.
- Importing legacy localStorage sessions. ASKCHAT-009.
- Reworking the visual design beyond what streaming, Stop, and a server-backed list require. The layout work in this repository is its own review.
- Optimistic thread creation before the server answers. A thread exists once the route returns it.
- Any change to the Search page, the admin chat, or the Optimization screen.

## Notes
- Keep `pendingBySession` for the thinking card rather than replacing it with a runtime query: the runtime reports `isRunning` for the thread it owns, and a message that has not been sent yet is not yet a turn. The two together are what makes a not-asking thread usable.
- Regenerate is the transport's `reload` trigger, which the server resolves to "the newest turn" (ASKCHAT-004 AC 10). The page does not name a turn id, because the AI SDK's reload drops the message it replaces.
- The delete dialog's copy is not decoration: D-9 means the conversation goes and the votes stay, and a member who is told "delete" without that distinction will reasonably expect their feedback to vanish too.
- The 50-item cap is a display cap. Do not add a "load more" that the server does not support, and do not imply loss in the copy.

## Definition of done
- [ ] Code complete
- [ ] `web/src/test/ask-chat-page.test.tsx` green through the real runtime and stubbed frames
- [ ] `web/src/test/pages.test.tsx` green, with only the control-level changes AC 15 allows
- [ ] `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build` pass
- [ ] Committed on its own before ASKCHAT-008 starts

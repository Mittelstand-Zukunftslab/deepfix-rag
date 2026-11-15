# CHAT-018 — The Assistant UI composer

## Type
Feature

## Status
Done

## Summary
The message box becomes Assistant UI's composer: a rounded, auto-growing surface docked under the conversation, with a send button that turns into stop while a reply streams, Enter to send, Shift+Enter for a newline, and a one-line disclaimer underneath.

## PRD traceability
- Related: FR-022 (compose and send), FR-025 (labelled composer, named controls)
- Decisions: D-7 (Assistant UI on the Vercel AI SDK)
- Out-of-PRD: the visual target (owner request, 2026-11-15).

## Dependencies
- Requires: CHAT-016 (the shared composer surface), CHAT-017 (the layout the composer sits in).
- Blocks: CHAT-019 (the empty state offers prompts into this composer).

## Acceptance criteria

1. Given the registered components, when `web/src/components/assistant-ui/` is read, then it contains the Assistant UI `elements-composer` registry item (installed with `npx assistant-ui@latest add elements-composer`), and the composer surface tokens (`paper`, `ghostButton`, `inkButton`) come from the installed `elements/surfaces.tsx` rather than a new palette.
2. Given a live thread, when the composer renders, then it is built from `ComposerPrimitive.Root`, `ComposerPrimitive.Input`, `ComposerPrimitive.Send`, and `ComposerPrimitive.Cancel`, wrapped in the element's `ComposerToolbar`/`ComposerActions`, and no `<textarea>`/`<form>` is hand-written for it.
3. Given the textarea, when text is typed, then the field grows with the content up to a maximum height and then scrolls, and its accessible name is `Message the assistant`.
4. Given the field, when Enter is pressed without a modifier, then the message is sent; when Shift+Enter is pressed, then a newline is inserted and nothing is sent.
5. Given an empty field, when nothing has been typed, then the send control is disabled; given a reply streaming, then the send control is replaced by one whose accessible name is `Stop generating` and pressing it aborts the turn and keeps the partial text.
6. Given the composer, when it renders, then a single-line disclaimer sits under it stating that the assistant can only propose a run and nothing starts without confirmation, and the line is not an interactive control.
7. Given the bar and the line under it, when the composer renders, then the bar is framed by the Ask page's shared `ComposerFrame` and the line is the shared `ComposerHint` (CHAT-016), so the two chats have one composer surface, one radius, and one hint style rather than two.
8. Given no thread is open yet, when the page renders, then the same composer component renders in its one-thread-less form — the same `ComposerFrame`, the same send control, the same hint, the same accessible names — and sending creates the thread first and then sends the text. There is no second composer implementation for this state.
9. Given the composer, when a turn finishes, then focus returns to the textarea.
10. Given `web/src/test/admin-chat-session.test.tsx`, when it runs, then sending a message drives the same `POST /api/admin/chat/completions` body as before, and a new case asserts Shift+Enter inserts a newline while Enter sends.

## Out of scope
- Attachments, voice, dictation, model picker, slash commands, mentions, token counter: the chat has no backend for them and the story does not add one.
- Thread creation on every keystroke; a draft composer creates a thread only on send.

## Notes
- `ComposerPrimitive.Input` is already an auto-resizing textarea with `submitMode: "enter"` as its default, so auto-grow and Enter/Shift+Enter are the primitive's behaviour, not a new key handler.
- The live composer keeps Assistant UI's `ComposerPrimitive.Input` for its auto-grow and submit semantics and the shared `ComposerFrame` for its surface; the inner toolbar (`ComposerToolbar`/`ComposerActions`) comes from the installed element. Nothing is copied between the two chats: the surface is shared, the behaviour is each chat's own.
- The thread-less composer is the same component with no runtime behind it, exactly the split the installed `elements-composer` documents (a runtime lane and a standalone lane sharing one shell). It lives in the page, not in a second file, so the two states cannot drift.

## Definition of done
- [x] Code complete
- [x] `bun run typecheck`, `bun run lint`, `bun run test` pass
- [ ] Story committed on `feat/hermes-optimizer` before CHAT-019 starts

## Verification note

`chat-composer.tsx` is now `ComposerPrimitive.Root` + `ComposerPrimitive.Input` + `AuiIf` swapping `Send` for
`Cancel`, inside the shared `ChatComposerBar`. Auto-grow and Enter/Shift+Enter are the primitive's own behaviour;
`chat-session.tsx` lost its focus helper because the primitive moves focus back to the input when a run starts
and the page no longer owns the textarea.

`admin-chat-session.test.tsx` gained one case: a Shift+Enter keydown posts nothing, an Enter keydown posts one turn,
the control becomes **Stop generating** while it streams, and pressing it returns the composer to **Send message**.
The send control's accessible name is now `Send message`, which is the one assertion the existing send helper
needed.

The thread-less state is `DraftComposer`: the same bar and hint with a read-only field and a **Start a
conversation** control, because there is no runtime to send through until a thread exists. `admin-chat-page.test.tsx`
covers that path and asserts it creates a thread without calling the completions route.

`bun run typecheck`, `bun run lint` (0 errors), and `bun run test` (103 cases) pass. `bun run build` is run before the
last story of the set, with CHAT-019 … CHAT-023 on the same tree.

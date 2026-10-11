# 10 — Vote on passages and answers

**What to build:** On Search and Ask, a member marks a passage or an answer thumbs up or thumbs down and can trust the vote was kept. Votes are an append-only log. The current vote is the member's latest event, it survives a page refresh, and it still refers to the snapshotted passage after the index is rebuilt. The API confirms the vote as soon as it is committed locally.

**Blocked by:**

- 07 — Search the shared corpus
- 09 — Ask a question

**Status:** ready-for-agent

- [ ] Every passage on Search and Ask, and every answer on Ask, shows Thumbs up and Thumbs down, each with an accessible label ("Mark passage as relevant", "Mark passage as not relevant", "Mark answer as good", "Mark answer as bad").
- [ ] Feedback accepts an interaction id, a target (a passage with its rank, or the answer), and an action (up, down, or clear).
- [ ] A passage vote is keyed by user, interaction, and rank, and refers to the snapshotted document id, passage key, and text for that rank, so it stays valid after the index is rebuilt.
- [ ] Votes are stored as an append-only event log. A user's current vote on a target is their latest event. Clear removes the current vote without deleting history.
- [ ] The API returns 200 as soon as the event is committed to the product database. It does not wait for MLflow.
- [ ] The filled thumb shows the current vote after a page refresh. Pressing the filled thumb sends clear. Pressing the other thumb switches the vote.
- [ ] Typecheck passes.
- [ ] Verify in the browser.

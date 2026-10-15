# 09 — Ask a question

**What to build:** A signed-in member asks one question and reads an answer grounded in the shared corpus, with the passages it used shown underneath. The question, the answer, and the hit snapshot are stored and traced the same way as a search, so votes can attach to either the answer or a passage. Each ask is a single turn.

**Blocked by:**

- 07 — Search the shared corpus

**Status:** ready-for-agent

- [ ] Ask accepts a query and calls the query service's answer method.
- [ ] The API records an interaction with kind ask, the generated answer, and the hit snapshot in the same shape as a search, and traces the request in MLflow the same way.
- [ ] The response returns the interaction id, the answer, the current user's answer vote, and the hits in the search shape.
- [ ] The Ask screen shows the answer first and the passages it used underneath. It replaces the mock Chat screen, whose mock service is removed.
- [ ] If generation fails after retrieval succeeded, the response is HTTP 200 with a null answer and an error message, and the screen shows the error above the passages.
- [ ] Each ask is a single turn. No earlier question is sent as conversation memory.
- [ ] Typecheck passes.
- [ ] Verify in the browser.

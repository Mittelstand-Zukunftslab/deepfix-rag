# 12 — Feedback evaluation dataset and admin summary

**What to build:** Passage votes become a dev and holdout evaluation dataset that can fairly score any candidate config. Votes are netted per passage, then rolled up to documents: a document is relevant for a question when any of its passages is up-voted on balance, and negative when none is up and at least one is down. Ties, unvoted documents, deleted documents, and anything the champion produced on its own are not labels. Dev is written into the workspace where the agent can read it. Holdout is never written anywhere; it is computed in memory for the promotion gate. Answer votes are reported as an approval rate, not used as labels.

**Reference:** tasks/prd-product-ui.md, `### US-012: Build the feedback evaluation dataset`

**Blocked by:**

- 02 — Multi-label, reference-optional eval records
- 10 — Vote on passages and answers
- 11 — Mirror votes to MLflow through an outbox

**Status:** ready-for-agent

- [ ] The export groups interactions by normalized query text (Unicode NFKC, trimmed, lowercased, whitespace collapsed). One normalized query is one eval question, with an id of `fb_` plus the first 12 hex characters of the SHA-256 of the normalized query.
- [ ] For each question and passage key, the net vote is current up-votes minus current down-votes across all users and interactions. A passage is up when its net vote is positive and down when it is negative.
- [ ] For each question, a non-deleted document is relevant when at least one of its passages is up, negative when none of its passages is up and at least one is down, and unlabeled otherwise. A deleted document is dropped from both lists.
- [ ] A question is included when it has at least one relevant document or at least one negative document. A question whose judged documents were all deleted is excluded.
- [ ] Rows have query id, query, relevant document ids, and negative document ids, and no answer field. Both document lists are always present, and either may be empty. The champion's own answer text and its unvoted top hit are never used as labels.
- [ ] A question goes to holdout when the integer value of the SHA-256 of the normalized query, modulo 100, is below 20. Otherwise it goes to dev. The split is stable as new votes arrive.
- [ ] Dev is written under the workspace feedback directory for that snapshot. Holdout is never written to disk or to MLflow. The export returns it as in-memory records.
- [ ] Each export is logged as an MLflow run in experiment `deepfix_product_feedback` with the dev file as an artifact and parameters for the dev count, holdout count, snapshot id, and a SHA-256 of the sorted holdout query ids.
- [ ] Answer votes are not eval labels. They are reported as an answer approval rate (up divided by up plus down) overall and per champion config, and the list of down-voted answers is attached to the export run for diagnosis.
- [ ] The admin feedback summary returns vote counts by target and polarity, labeled dev and holdout question counts, answer approval rate, unsynced and no-trace event counts, and the MLflow experiment URLs. Members receive 403.
- [ ] Unit tests cover per-passage net votes, a down-voted passage not cancelling an up-voted passage of the same document, a negative-only document, ties left unlabeled, deleted-document exclusion, inclusion of a negative-only question, exclusion of a question with no remaining labels, split stability, and that no export path writes holdout rows to a file.

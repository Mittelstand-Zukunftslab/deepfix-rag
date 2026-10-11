# 12 — Feedback evaluation dataset and admin summary

**What to build:** Passage votes become a dev and holdout evaluation dataset that can fairly score any candidate config. A document is relevant when up-votes exceed down-votes and negative when down-votes exceed up-votes. Ties, unvoted documents, deleted documents, and anything the champion produced on its own are not labels. Dev is written into the workspace where the agent can read it. Holdout is written only in the API-private directory. Answer votes are reported as an approval rate, not used as labels.

**Blocked by:**

- 02 — Multi-label, reference-optional eval records
- 10 — Vote on passages and answers
- 11 — Mirror votes to MLflow through an outbox

**Status:** ready-for-agent

- [ ] The export groups interactions by normalized query text (Unicode NFKC, trimmed, lowercased, whitespace collapsed). One normalized query is one eval question, with an id of `fb_` plus the first 12 hex characters of the SHA-256 of the normalized query.
- [ ] For each question and document, the export counts current passage up-votes and down-votes across all users and interactions. A non-deleted document is relevant when up-votes exceed down-votes, and negative when down-votes exceed up-votes. A tie, or a document with no votes, is unlabeled. A deleted document is dropped from both lists.
- [ ] A question is included when it has at least one relevant document or at least one negative document. A question whose judged documents were all deleted is excluded.
- [ ] Rows are written with query id, query, relevant document ids, and negative document ids, and no answer field. Both document lists are always present, and either may be empty. The champion's own answer text and its unvoted top hit are never used as labels.
- [ ] A question goes to holdout when the integer value of the SHA-256 of the normalized query, modulo 100, is below 20. Otherwise it goes to dev. The split is stable as new votes arrive.
- [ ] Dev is written under the workspace feedback directory for that snapshot. Holdout is written only under the API-private data directory, outside the workspace.
- [ ] Each export is logged as an MLflow run in experiment `deepfix_product_feedback` with both files as artifacts and parameters for the dev count, holdout count, and snapshot id.
- [ ] Answer votes are not eval labels. They are reported as an answer approval rate (up divided by up plus down) overall and per champion config, and the list of down-voted answers is attached to the export run for diagnosis.
- [ ] The admin feedback summary returns vote counts by target and polarity, labeled dev and holdout question counts, answer approval rate, unsynced event count, and the MLflow experiment URLs. Members receive 403.
- [ ] Unit tests cover relevant-versus-negative aggregation, ties left unlabeled, deleted-document exclusion, inclusion of a negative-only question, exclusion of a question with no remaining labels, and split stability.

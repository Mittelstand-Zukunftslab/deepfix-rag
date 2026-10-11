# 11 — Mirror votes to MLflow through an outbox

**What to build:** Every committed vote is mirrored to MLflow as human feedback on the trace of the request it judges. A changed vote updates the existing assessment, a cleared vote removes it, and a vote is never lost when MLflow is briefly unreachable. Admins can see how many events have been waiting to sync for more than five minutes.

**Blocked by:**

- 10 — Vote on passages and answers

**Status:** ready-for-agent

- [ ] An outbox worker mirrors each vote event to MLflow feedback on the interaction's trace: name `passage_relevance` (with document id and rank in metadata) or `answer_quality`, boolean value, source type human, source id equal to the user id.
- [ ] A change updates the existing assessment. A clear removes it.
- [ ] Unsynced events are retried with backoff. Events unsynced for more than 5 minutes are counted in the admin feedback summary.
- [ ] The vote endpoint still returns as soon as the local event is committed and does not wait for this mirror.

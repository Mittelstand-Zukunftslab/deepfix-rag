# 02 — Multi-label, reference-optional eval records

**What to build:** `deepfix eval` scores questions that have several relevant documents, explicit negative documents, and no reference answer, so product feedback can be judged without inventing labels. Existing benchmark files still load and score exactly as they do today.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] An eval record accepts a list of relevant document ids, an optional list of negative document ids, and an optional reference answer. A record loaded with a single document id string produces that id as its only relevant document and no negatives, so every existing benchmark file loads unchanged.
- [ ] JSONL rows may carry either a single document id or a list of document ids. A row with both keys is rejected with exit code 1. The same document id in both the relevant list and the negative list is rejected with exit code 1.
- [ ] When negatives are absent, scoring stays as it is today: the listed documents are relevant, and every retrieved document outside that list is non-relevant.
- [ ] When negatives are present, labels are binary and judged-only. Listed documents are relevant. Negative documents are negative. A retrieved document in neither list is unjudged and is left out of precision and nDCG. Retrieving a negative document lowers precision and nDCG. Recall uses only the relevant documents. A row with an empty relevant list omits recall and is not treated as unanswerable.
- [ ] For rows with no reference answer, metrics that need one (answer correctness, and DeepEval metrics that need an expected output such as contextual precision and contextual recall) are skipped for that row and not averaged into the summary. Reference-free metrics (faithfulness, answer relevancy) still run.
- [ ] A metric skipped for every row is absent from the comparison delta. A missing delta is already treated as "not an improvement" by the optimization loop.
- [ ] A row is unanswerable only if it is explicitly marked so, not merely because the answer is missing.
- [ ] Unit tests cover a single document id, multiple document ids, a missing answer, a row with both keys, a retrieved negative lowering precision, an unjudged hit left out of precision, a negative-only row that is not unanswerable, and an unchanged benchmark row with no negatives.
- [ ] `uv run --env-file .env pytest -v -m "not network and not e2e"` passes.

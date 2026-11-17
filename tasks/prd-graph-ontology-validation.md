# PRD validation

- PRD path: `tasks/prd-graph-ontology.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

Validated with the `validate-prd` skill from
`https://github.com/Mittelstand-Zukunftslab/awesome-skills`, fetched at
`/tmp/awesome-skills/validate-prd/SKILL.md` and applied as written, with one
adaptation: the skill's output path is `docs/prd-validation.md`, and this
repository keeps product documents in `tasks/`, so the report lives beside the
PRD as `tasks/prd-graph-ontology-validation.md`.

## Summary scores

| Dimension | Status | Notes |
| :--- | :--- | :--- |
| Completeness | Green | Overview, goals and non-goals, personas, scope with an explicit "Won't", three journeys, 24 FRs, 7 NFRs, 8 release criteria, dependencies, 6 risks, traceability, decisions, glossary, open questions. |
| Consistency | Amber | The PRD says the generic fallback keeps user uploads working (FR-006, NFR-006) while §4 puts other corpora out of scope. Reconciled: the fallback is preserved, not re-designed. |
| Testability | Green | Every Must FR names an observable: a key in `deepfix schema --json`, a validation message naming two keys, a triple present in the registry, a file at a known path, a payload field, a gate reason string, a test that runs in CI. |
| Traceability | Green | Every FR maps to a goal or a settled decision in §11; no orphan FR and no orphan goal. |
| Feasibility | Green | The llama-index surface (`kg_validation_schema` accepting `List[Triple]`, `num_workers`, `CustomPGRetriever`, `triplet_source_id`, `(c:Chunk)` + `MENTIONS`) was checked against the installed 0.14.25 / falkordb 0.7.0 packages and a live FalkorDB, not assumed. |
| Security & privacy | Green | Public corpus, no PII beyond public figures' names, no secret logged (NFR-003), FalkorDB auth unchanged. |

## Findings

### Blockers (must fix before story breakdown)

None. Every Must FR is observable, and no FR contradicts a non-goal.

### Should-fix

1. **FR-015's weight rule was underspecified for the zero case.** "weighted by
   `dense_weight` and `graph_weight`" leaves `(0, 0)` undefined, and a config
   that sets neither would divide by zero. Fixed in the PRD text: equal weights
   when both are zero.
2. **FR-014 did not say what happens to a triplet whose source chunk is gone.**
   After a deduplication merge the `MENTIONS` edge moves but the chunk stays, so
   the case is a stale build rather than a normal one. Fixed by stating that the
   mapping skips a triplet with no resolvable chunk, and that this is a dropped
   candidate rather than an error.
3. **FR-017's "both indexes" needed a stated location.** A FalkorDB graph has no
   directory and the LanceDB chunks do. Fixed: the LanceDB table stays at
   `retrieval.lancedb_index_dir` and the merge log and manifest live beside it,
   which is what the manifest's index-time signature already assumes.
4. **The release criteria did not cover the `mixed` regression risk.** Adding
   `graph_hybrid` to `_ALLOWED_BACKENDS` in two places invites a careless
   `frozenset` edit that also admits `mixed`. Added to AC 6: `graph` **and**
   `mixed` are rejected with the existing reason.
5. **The `rapidfuzz` dependency was assumed, not stated as a change.** It is not
   in `pyproject.toml` today. Added to §9 as an explicit new dependency with its
   test consequence (`uv lock` must be regenerated, and `test_lockfile.py` guards
   it).

### Nice-to-have

- §6.2's relation table could carry one worked example per relation from the
  sample corpus. The corpus text is generic legal prose rather than real DIP
  papers, so an example would be invented. Left out deliberately.
- NFR-001's 700–900 chunk estimate is arithmetic, not measurement; §14 records
  it as an open question owned by the first build.

## Suggested edits (concrete)

- FR-015: add "identical weights when both are zero".
- FR-014: add "a triplet whose source chunk cannot be loaded is skipped; it is a
  dropped candidate, not a retrieval error".
- FR-017: name `retrieval.lancedb_index_dir` as where the chunk index and the
  merge log live.
- AC 6: assert both `graph` and `mixed` are rejected.
- §9: list `rapidfuzz` as a new runtime dependency.

All five were applied to `tasks/prd-graph-ontology.md` before the stories were
cut.

## Questions for authors

None. The seven decisions in §12 are settled by Fadel and are not re-opened by a
story.

## PRD quality gates

| Gate | Result |
| :--- | :--- |
| Every **Must** FR has testable acceptance wording | Pass |
| No duplicate FRs under different ids | Pass — FR-015 (merge) and FR-016 (rerank) are separable stages, cross-referenced |
| Non-goals do not contradict Must FRs | Pass — the Ask non-goal is "no change to its contract", and FR-014/FR-015 satisfy it rather than extend it |
| Terms are glossary-consistent | Pass — §13 defines the six domain nouns used in FR-004…FR-012 |
| Security/privacy touched where applicable | Pass — NFR-003 |
| Each FR maps to a wish or goal | Pass — §11 |

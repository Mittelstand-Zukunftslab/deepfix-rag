# Story validation: GRAPH-006 — The shipped graph config, docs, and the FalkorDB CI job

- Story file: `tasks/stories-graph-ontology/GRAPH-006-shipped-config-docs-and-ci.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. The story is one outcome — a person can run the graph path from a shipped
file and CI keeps it working — and every criterion names a file, a key, a job, or
a test case.

### Should-fix

1. **AC 5 was unfalsifiable as written.** "the two must agree exactly" asks the
   tester to compare two lists by eye. Fixed by naming the machine check:
   `deepfix schema --json`'s `index_time_keys` is the list the README must
   contain, and the assertion is set equality against
   `index_time_signature(RAGConfig())`'s key names. `tests/test_harness.py`
   already reads that payload, so the check has a home.
2. **AC 9 did not say the job also lints the new modules.** The `cli` job runs
   `ruff check` with no arguments over the whole tree, so it already covers a new
   module — but a reader adding a job may not notice. Fixed by stating it, so the
   `graph` job fails on its own for a formatting problem in the new files rather
   than only in `cli`.
3. **AC 8's skip/fail switch was stated twice** (in the story and in the PRD).
   Kept in both, because the PRD states the requirement and the story states the
   environment variable name; duplicating a name is safer than a reader inventing
   one.
4. **The `prd-deepfix-rag.md` edit had no boundary.** "its graph sections are
   corrected" invites a rewrite of a historical document. Fixed by naming exactly
   three places (the top note, §4.4, the paradigm list and FR-11) and by saying
   the checklist line is updated rather than deleted.
5. **AC 2's reason was missing.** A reader could conclude the two old configs
   should be deleted. Added: their corpus is the Bundestag one, so the ontology
   applies to them too, and they stay as the `graph`-backend references.

### Nice-to-have

- The story could publish the first build's measured numbers. It deliberately
  does not: the PRD's §14 records the chunk-count estimate as an open question,
  and a measurement belongs in the run artifact, not in a story about shipping
  the capability.

## Suggested edits (concrete)

- AC 5: assert set equality between the README list and
  `index_time_signature(RAGConfig())`'s key names via `deepfix schema --json`.
- AC 9: state that the `graph` job runs `ruff check` and
  `ruff format --check` over the tree.
- AC 2: add "their corpus is the Bundestag one, so the ontology applies".
- AC 6: name the three places and say the checklist line is updated, not deleted.

All four were applied to the story before implementation.

## PRD alignment

- FRs: FR-020 (ACs 3–4), FR-022 (ACs 7–8), FR-023 (ACs 9–11), FR-024 (the absence
  of narrow unit tests: the module holds three integration cases and no
  per-function cases).
- Gaps: none.

## Dependencies

GRAPH-003, GRAPH-004, and GRAPH-005 are named as prerequisites and the order is
acyclic: 003 and 004 both need 002, 005 needs 004, and this story needs all
three. `rapidfuzz` (GRAPH-003) must be locked before the CI job can install.

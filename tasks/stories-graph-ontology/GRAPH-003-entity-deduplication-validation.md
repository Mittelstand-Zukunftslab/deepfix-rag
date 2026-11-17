# Story validation: GRAPH-003 — Entity deduplication as a logged post-extraction step

- Story file: `tasks/stories-graph-ontology/GRAPH-003-entity-deduplication.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. The story is one outcome — merges are made, re-pointed, and logged — and
each criterion names a node, an edge, a file, a key, or a count.

### Should-fix

1. **AC 9 was self-contradictory.** "the file exists and is empty (or is absent
   if no merge was attempted — the two are distinguishable from the payload)" is
   OR-soup: a tester cannot tick it. Fixed by making the file always exist from
   the first deduplication pass, empty when nothing merged, so the assertion is
   `payload["graph_merges"] == 0 and log.exists() and log.read_text() == ""`.
2. **AC 7's "every relationship … now points at" is unverifiable without naming
   how to read them.** Fixed by naming the query the test uses:
   `MATCH (e:__Entity__)-[r]->(o) RETURN type(r)` for both endpoints, plus the
   `MENTIONS` check.
3. **The canonical-name rule and the alias property were in different criteria**
   (AC 6 and AC 7), and AC 6 did not say whether "longest" counts characters
   before or after lower-casing. Fixed: length of the original name, ties broken
   by `sorted()` on the lower-cased form.
4. **The digit guard had no stated edge case.** `§ 315c` vs `§ 315d` has one
   digit run each and they differ (`315` vs `315`), so a naive digit-tuple
   comparison would let them merge. Fixed by stating that the *trailing*
   alphanumeric token is part of the comparison: the guard compares the digit
   runs **and** the full lower-cased name's non-space alphanumerics, which
   distinguishes `315c` from `315d`. This also keeps `20/123` and `20/124`
   apart, where the digit runs already differ.
5. **The embedding count in Notes was not tied to a bound.** "at most 4,000 short
   embeddings" reads as a promise about the corpus. Fixed: one embedding per
   distinct `(label, lower-cased name)`, and the number is the number of distinct
   names.

### Nice-to-have

- The log could carry the build id so two builds' logs can be told apart. The
  PRD puts each build in its own index directory (NFR-005), which already does
  that; left out.

## Suggested edits (concrete)

- AC 9: "the log file exists and is empty when nothing merged".
- AC 7: name the `MATCH (e:__Entity__)-[r]->(o)` read.
- AC 6: canonical name is the longest **original** name; ties by `sorted()`
  order of the lower-cased names.
- AC 4 / Notes: the identifier guard compares digit runs **and** the trailing
  alphanumeric token, so `315c`/`315d` stays apart.
- Notes: one embedding per distinct `(label, lower-cased name)`.

All five were applied to the story before implementation.

## PRD alignment

- FRs: FR-009 (the predicate, ACs 2–3), FR-010 (no identifier merges, AC 4;
  transitivity and canonical name, AC 6), FR-011 (the log, ACs 8–9), FR-012
  (re-pointing and deletion, AC 7), FR-002 (the three keys, ACs 2–3).
- Gaps: none. FR-012's "keeps the duplicate's name as an alias property" is AC 7.

## NFR touchpoints

- NFR-003: the log holds corpus entity names only, no secret.
- NFR-005: the log is per-index-directory, so two builds cannot interleave.

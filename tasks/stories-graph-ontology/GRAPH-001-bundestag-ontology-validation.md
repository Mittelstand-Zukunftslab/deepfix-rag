# Story validation: GRAPH-001 — A Bundestag ontology selected by `graph_extraction_schema`

- Story file: `tasks/stories-graph-ontology/GRAPH-001-bundestag-ontology.md`
- Validated: 2026-11-17
- Verdict: **Pass with actions**

## Findings

### Blockers

None. Every criterion names an inspectable value: a registry key, an exact list,
an exact triple count, or a test that runs.

### Should-fix

1. **AC 7 was ambiguous about which pairs it generates.** "the complete cross
   product of their own entity types for each of their own relations" reads two
   ways: (relation × type pair) or (type pair × relation). Fixed to the first,
   which is what `entity_relationship` and `domain_legal` need to stay
   permissive without becoming untyped.
2. **AC 9 said "logs no error" without naming where.** A reader cannot check it.
   Fixed to name the resolver: the registry lookup returns the
   `entity_relationship` entry, and the pipeline emits no warning and does not
   raise.
3. **The `validation_triples` field default was not tied to behaviour.** Added to
   Notes: an empty list means no pair restriction, and the pipeline passes
   `None`, so a registry entry written before this change keeps extracting the
   way it does today. Without that, adding the field would silently empty every
   graph built from a third-party schema.

### Nice-to-have

- The story could name the 23 triples inline instead of pointing at PRD FR-005.
  Duplicating them would create a second copy that can drift, so the pointer
  stands and AC 5 fixes the count, which is the checkable part.

## Suggested edits (concrete)

- AC 7: "for each relation in the schema, every ordered pair of that schema's
  own entity types".
- AC 9: "the resolver returns the `entity_relationship` entry, no exception is
  raised, and no warning is emitted".
- Notes: add the empty-list/`None` rule.

All three were applied to the story before implementation.

## PRD alignment

- FRs: FR-001, FR-004, FR-005, FR-006 — all present in PRD §6.2.
- Gaps: none. FR-006 ("strict for every entry") is AC 8; FR-001's fallback
  clause is AC 9.

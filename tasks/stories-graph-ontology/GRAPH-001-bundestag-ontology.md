# GRAPH-001 — A Bundestag ontology selected by `graph_extraction_schema`

## Type

Feature

## Status

Draft

## Summary

A developer adding `retrieval.graph_extraction_schema: bundestag` to a config
gets a closed, corpus-specific vocabulary: nine Bundestag DIP entity types and
nine relations, with an explicit list of which relation may join which type
pair. The generic `entity_relationship` schema stays as the fallback for
user-uploaded documents, and both schemas are strict, so a triplet outside the
allowed set is discarded instead of written to the graph.

## PRD traceability

- Related: FR-001, FR-004, FR-005, FR-006
- Goals: Goal 1 (define the ontology, selectable), decisions 1 and 2

## Acceptance criteria

1. Given a config with `retrieval.graph_extraction_schema: bundestag`, when
   `RAGConfig.from_yaml` loads it, then it validates and
   `GRAPH_EXTRACTION_SCHEMAS["bundestag"]` is the entry the pipeline reads.
2. Given `GRAPH_EXTRACTION_SCHEMAS`, when it is inspected, then it has exactly
   the keys `entity_relationship`, `domain_legal`, and `bundestag`.
3. The `bundestag` entry's `entities` are exactly `PERSON`, `FRAKTION`,
   `MINISTERIUM`, `AUSSCHUSS`, `DRUCKSACHE`, `PLENARPROTOKOLL`, `VORGANG`,
   `GESETZ`, `THEMA`.
4. The `bundestag` entry's `relations` are exactly `MEMBER_OF`, `SUBMITTED_BY`,
   `ANSWERED_BY`, `ASSIGNED_TO`, `PART_OF`, `AMENDS`, `REFERS_TO`, `SPOKE_IN`,
   `ABOUT`.
5. The `bundestag` entry's validation schema is a list of
   `(subject type, relation, object type)` triples and holds exactly the 23
   triples listed in PRD FR-005, no more and no fewer.
6. Every triple's three parts appear in that entry's `entities`/`relations`
   lists; a triple naming an undeclared type or relation is absent.
7. `entity_relationship` and `domain_legal` also carry a validation schema: for
   each relation in the schema, every ordered pair of that schema's own entity
   types, so strict mode never rejects a pair those schemas used to accept.
8. Every schema entry resolves to `strict=True` in the extractor — there is no
   per-schema `strict` switch to turn off.
9. Given `retrieval.graph_extraction_schema: default` or an unknown value, when
   the pipeline resolves the schema, then the resolver returns the
   `entity_relationship` entry, no exception is raised, and no warning is
   emitted.
10. The existing test `tests/test_config_mutator.py::test_graph_extraction_schemas_in_schema_module`
    is extended to assert ACs 2–6, and it passes.

## Out of scope

- Any change to extraction itself (worker count, document sample). That is
  GRAPH-002.
- Choosing the prompt. The extractor's default prompt is used.
- The shipped config file that sets `bundestag`. That is GRAPH-006.

## Notes

The triple list is the form the installed `llama-index-core` 0.14.25 accepts
natively: `SchemaLLMPathExtractor.__init__` takes
`kg_validation_schema: Optional[Union[Dict[str, str], List[Triple]]]` and wraps a
list as `{"relationships": [...]}` itself. The dict-of-lists form in the
LlamaIndex blog post is the older, APOC-era shape and is a fallback branch in the
same constructor; the list form is preferred here so the pair list is data
rather than a nesting of names.

`GraphExtractionSchema` gains one field, `validation_triples: list[tuple[str,
str, str]]`, defaulting to empty. An empty list means "no pair restriction": the
pipeline passes `None` to `kg_validation_schema` in that case, so a registry
entry written before this change keeps extracting exactly what it extracts today.

## Definition of done

- [ ] Code complete
- [ ] Test extended and passing
- [ ] `uv run ruff check` and `uv run ruff format --check` clean
- [ ] Docs updated where the schema list is described (`configs/README.md` is
      GRAPH-006's; this story touches no user-facing doc)

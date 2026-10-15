---
name: legal-retrieval-grounding
description: >
  BENCHMARK / MANUAL USE ONLY, NEVER IN API-DRIVEN RUNS (graph backends are rejected
  by the promotion gate and need graph hosts the agent does not have). Use on Legal
  RAG Bench when retrieval_miss or retrieval_induced_error comes from missed statute
  citations. Set retrieval.backend=graph with graph_extraction_schema=domain_legal and
  re-index. Hierarchical chunking is the structure lever for long statutes.
failure_mode:
  - retrieval_miss
  - retrieval_induced_error
trigger_conditions:
  - "Benchmark / manual use only: the dataset is Legal RAG Bench and recall stays low because statute citations or article ids are missed"
  - "Benchmark / manual use only: failure_distribution.retrieval_miss stays high after dense and rerank trials"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=graph --set retrieval.graph_store=memgraph --set retrieval.graph_extraction_schema=domain_legal --set retrieval.language=english --json"
  - "deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-graph --force --json"
  - "deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-graph --compare configs/champion.yaml --dataset <dataset_path> --retrieval-only --limit 30 --json"
---

BENCHMARK / MANUAL USE ONLY, NEVER IN API-DRIVEN RUNS: graph and mixed backends are rejected by the promotion gate and need graph hosts the agent does not have. The loop's routing table does not route here.

For a developer on a benchmark: `<dataset_path>` is the benchmark QA dataset (for example `HF:isaacus/legal-rag-bench`) and `<run_dir>` is any scratch directory for the candidate and index. Follow `optimization-loop` for the shape of the gate and the exit codes. Graph extraction runs at index time and calls an LLM, so it needs a reachable graph database and an LLM key; do not use it inside a 15-step round.

## When not to use

If dense retrieval already returns the gold passage and `ndcg` is the weak metric, use `rerank-calibration`. Graph indexing is expensive and does not fix a pure ranking inversion.

## Procedure

The default `retrieval.graph_extraction_schema` is `default`, and that name matches no schema, so no entity list is applied. `domain_legal` is the legal schema (STATUTE, LAW, COURT, CASE, ARTICLE, PARAGRAPH, and relations such as CITES and AMENDS).

1. Point retrieval at a graph store and the legal schema. Set `retrieval.language` to the corpus language. Confirm `diffs` is non-empty.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=graph --set retrieval.graph_store=memgraph --set retrieval.graph_extraction_schema=domain_legal --set retrieval.language=english --json
```

2. Re-index. Triplet extraction will not run against an existing vector index.

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-graph --force --json
```

3. Compare. Primary metric is `recall` or `ndcg`.

```
deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-graph --compare configs/champion.yaml --dataset <dataset_path> --retrieval-only --limit 30 --json
```

For long statutes, a second index-time hypothesis is hierarchical chunking. `chunking.hierarchical_chunk_sizes` is a list; edit it in YAML rather than `--set`, then re-index. See `chunking-tuning`.

## Valid values

`retrieval.backend`: `graph` for this skill. Combining graph with dense and BM25 is `mixed` (`multi-hop-aggregation`).

`retrieval.graph_store`: `memgraph` or `falkordb`. `none` does not store a graph.

`retrieval.graph_extraction_schema`: `domain_legal` or `entity_relationship`. Any other string, including `default`, stores no allowed entity or relation list.

`retrieval.graph_max_paths_per_chunk`: 1–10 (default 5). Index-time.

`retrieval.language`: string passed to BM25 and graph retrieval (default `german`). Legal RAG Bench English corpora should set `english`.

## Index-time vs query-time

`retrieval.backend=graph`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, `retrieval.graph_max_paths_per_chunk`, and `chunking.*` are index-time. `retrieval.graph_traversal_hops` is query-time and capped at 3.

## Validated findings

No checked-in A/B result for `domain_legal` graph extraction yet. The measured legal-rag-bench gain is reranking; see `rerank-calibration`.

## Pitfalls

- Leaving `graph_extraction_schema` at `default` builds a graph with no legal entity vocabulary.
- Evaluating the previous LanceDB directory after this mutation does not load the new triples.
- `graph` retrieval expects Memgraph or FalkorDB to be reachable. A refused connection is exit code 2, not a bad hypothesis.

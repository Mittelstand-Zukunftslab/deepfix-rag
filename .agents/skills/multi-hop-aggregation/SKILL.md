---
name: multi-hop-aggregation
description: >
  BENCHMARK / MANUAL USE ONLY, NEVER IN API-DRIVEN RUNS (graph and mixed backends are
  rejected by the promotion gate and need graph hosts the agent does not have). Use when
  retrieval_induced_error comes from partial recall across multiple documents. Raise
  retrieval.graph_traversal_hops (max 3) on an existing graph, or switch to
  retrieval.backend=mixed and set graph_weight. Schema changes still require a re-index.
failure_mode:
  - retrieval_induced_error
  - retrieval_miss
trigger_conditions:
  - "Benchmark / manual use only: summary recall is partial (0 < recall < 1) on multi-document questions"
  - "Benchmark / manual use only: failure_distribution.retrieval_induced_error is high and gold evidence is split across documents"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.graph_traversal_hops=3 --json"
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=mixed --set retrieval.graph_store=memgraph --set retrieval.graph_weight=0.3 --set retrieval.dense_weight=0.5 --set retrieval.bm25_weight=0.2 --json"
---

BENCHMARK / MANUAL USE ONLY, NEVER IN API-DRIVEN RUNS: graph and mixed backends are rejected by the promotion gate and need graph hosts the agent does not have. The loop's routing table does not route here.

For a developer on a benchmark: `<dataset_path>` is the benchmark QA dataset and `<run_dir>` is any scratch directory for the candidate and index. Follow `optimization-loop` for the shape of the gate and the exit codes. `deepfix hpo --paradigm graph` is a separate long run and is not part of this skill.

## When not to use

A single gold passage found but ranked last is `rerank-calibration`. One document split into incoherent slices is `chunking-tuning`.

## Procedure

If a graph index already exists, hops are query-time. Confirm `diffs` is non-empty.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.graph_traversal_hops=3 --json
```

```
deepfix eval --config <run_dir>/candidate.yaml --compare configs/champion.yaml --dataset <dataset_path> --retrieval-only --limit 30 --json
```

To fuse graph with dense and BM25, switch to `mixed` and set all three weights. The retriever weights are always normalized to sum 1 (both `hybrid` and `mixed`), so they need not add up to 1 as long as the sum is positive. They only change the ranking under `fusion_mode=relative_score` or `dist_based_score`; the default `reciprocal_rerank` ignores them. This backend change needs a graph index.

```
deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=mixed --set retrieval.graph_store=memgraph --set retrieval.graph_weight=0.3 --set retrieval.dense_weight=0.5 --set retrieval.bm25_weight=0.2 --json
```

```
deepfix index --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-mixed --force --json
```

```
deepfix eval --config <run_dir>/candidate.yaml --index-dir <run_dir>/idx-mixed --compare configs/champion.yaml --dataset <dataset_path> --retrieval-only --limit 30 --json
```

## Valid values

`retrieval.graph_traversal_hops`: 1–3 (default 2). `3` is the maximum; `4` fails validation.

`retrieval.backend=mixed` uses dense, BM25, and graph together.

`retrieval.graph_weight`, `dense_weight`, and `bm25_weight`: each 0.0–1.0. Their sum must be positive; they are normalized to sum 1 (see Procedure).

`retrieval.query_expansion_num` also applies to `mixed` fusion and calls an LLM above 1. See `query-rewriting`.

## Index-time vs query-time

`graph_traversal_hops` is query-time (`path_depth` on the graph retriever). `retrieval.backend` moving to `mixed` or `graph`, `graph_store`, and `graph_extraction_schema` are index-time.

## Validated findings

No checked-in A/B result for hop count or mixed fusion yet.

## Pitfalls

- `graph_traversal_hops` on a vector-only index does nothing until a property graph has been built.
- Graph retrieval and extraction need a reachable Memgraph or FalkorDB and an LLM. A refused connection is exit code 2, not a bad hypothesis.
- A hop value above 3 makes `mutate-config` exit 1.

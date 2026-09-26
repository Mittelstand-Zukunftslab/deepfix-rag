---
name: multi-hop-aggregation
description: >
  Use when retrieval_induced_error comes from partial recall across multiple
  documents. Raise retrieval.graph_traversal_hops (max 3) on an existing graph,
  or switch to retrieval.backend=mixed and set graph_weight. Schema changes
  still require a re-index.
failure_mode:
  - retrieval_induced_error
  - retrieval_miss
trigger_conditions:
  - "context_recall or recall is partial: 0 < recall < 1 on multi-document questions"
  - "failure_distribution.retrieval_induced_error is high and gold evidence is split across documents"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.graph_traversal_hops=3 --json"
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.backend=mixed --set retrieval.graph_store=memgraph --set retrieval.graph_weight=0.3 --set retrieval.dense_weight=0.5 --set retrieval.bm25_weight=0.2 --json"
  - "uv run --env-file .env deepfix hpo --paradigm graph --retrieval-only --limit 30 --n-trials 10 --output configs/best_graph.yaml --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget. `hpo --paradigm graph` is a separate long run. Do not start it inside the 15-call iteration.

## When not to use

A single gold passage found but ranked last is `rerank-calibration`. One document split into incoherent slices is `chunking-tuning`.

## Procedure

If a graph index already exists, hops are query-time. Confirm `diffs` is non-empty.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.graph_traversal_hops=3 --json
```

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

To fuse graph with dense and BM25, switch to `mixed` and set all three weights. Weights that do not sum to 1 are renormalized only on the `mixed` backend. This backend change needs a graph index.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.backend=mixed --set retrieval.graph_store=memgraph --set retrieval.graph_weight=0.3 --set retrieval.dense_weight=0.5 --set retrieval.bm25_weight=0.2 --json
```

```
uv run --env-file .env deepfix index --config configs/candidate.yaml --index-dir indexes/candidate-mixed --force --json
```

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --index-dir indexes/candidate-mixed --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

Outside the iteration budget:

```
uv run --env-file .env deepfix hpo --paradigm graph --retrieval-only --limit 30 --n-trials 10 --output configs/best_graph.yaml --json
```

## Valid values

`retrieval.graph_traversal_hops`: 1–3 (default 2). `3` is the maximum; `4` fails validation.

`retrieval.backend=mixed` uses dense, BM25, and graph together.

`retrieval.graph_weight`, `dense_weight`, and `bm25_weight`: each 0.0–1.0. Their sum must be positive.

`retrieval.query_expansion_num` also applies to `mixed` fusion. See `query-rewriting`.

## Index-time vs query-time

`graph_traversal_hops` is query-time (`path_depth` on the graph retriever). `retrieval.backend` moving to `mixed` or `graph`, `graph_store`, and `graph_extraction_schema` are index-time.

## Validated findings

No checked-in A/B result for hop count or mixed fusion yet.

## Pitfalls

- `graph_traversal_hops` on a vector-only index does nothing until a property graph has been built.
- `hpo --paradigm graph` re-ingests with an LLM extractor and will blow the 15-call iteration if started there.
- A hop value above 3 makes `mutate-config` exit 1.

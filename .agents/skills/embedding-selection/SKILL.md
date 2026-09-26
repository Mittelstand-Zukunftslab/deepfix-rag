---
name: embedding-selection
description: >
  Use when retrieval_miss persists and dense, bm25, and hybrid have not been
  compared on the same index. The embedding model is the EMBEDDING_MODEL setting,
  not a RAGConfig field. PCA flags do not change the retrieval index.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "failure_distribution.retrieval_miss is high and chunk boundaries are already coherent"
  - "dense, bm25, and hybrid have not been compared with eval --retrieval-only --compare"
cli_actions:
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.backend=dense --json"
  - "uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.backend=bm25 --json"
  - "uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json"
---

Follow `optimization-loop` for the quality gate, exit codes, and the 15-call budget.

Do not set `data.enable_embedding_pca` or `data.embedding_pca_components` to improve retrieval. PCA is already on by default (200 components) and is only passed into the dataset built for `run-diagnostic`. It is not applied when `index` or `eval` embeds queries. There is no config key for the embedding model; that process uses the `EMBEDDING_MODEL` environment setting and requires a fresh index to take effect.

## When not to use

High `recall` with low `ndcg` is `rerank-calibration`. Cut-off passages are `chunking-tuning`. A dense index that works and a lexical query that does not is `query-rewriting`.

## Procedure

Compare backends on the same corpus. Changing among `dense`, `bm25`, and `hybrid` is query-time when the index still has node text. Confirm `diffs` is non-empty.

```
uv run --env-file .env deepfix mutate-config --base configs/champion.yaml --output configs/candidate.yaml --set retrieval.backend=dense --json
```

```
uv run --env-file .env deepfix eval --config configs/candidate.yaml --compare configs/champion.yaml --retrieval-only --limit 30 --json
```

Repeat with `--set retrieval.backend=bm25` and `--set retrieval.backend=hybrid`. Promote only the backend that clears the optimization-loop gate. If hybrid wins on `ndcg` only after rerank, keep the rerank skill's settings rather than fusion weights alone.

To change the embedding model, set `EMBEDDING_MODEL`, then rebuild:

```
uv run --env-file .env deepfix index --config configs/candidate.yaml --index-dir indexes/candidate --force --json
```

## Valid values

`retrieval.backend`: `bm25`, `dense`, `hybrid`, `graph`, `mixed`. This skill compares the first three. `graph` and `mixed` are index-time and belong to `legal-retrieval-grounding` or `multi-hop-aggregation`.

## Index-time vs query-time

`retrieval.backend` among `dense`, `bm25`, and `hybrid` is query-time if node text loads. If stderr contains `falling back to dense`, re-index with `index --force`. Changing `EMBEDDING_MODEL` is index-time. PCA fields are diagnostic-only.

## Validated findings

No checked-in A/B result that isolates the embedding model. Related result, from `rerank-calibration`: on legal-rag-bench, dense-only beat a naive 50/50 hybrid until reranking was enabled.

## Pitfalls

- `data.enable_embedding_pca=true` does not compress the LanceDB index.
- An unknown key such as `embedding.model` is dropped and `diffs` stays empty.
- Scoring `bm25` or `hybrid` against an empty docstore silently runs dense retrieval, so the comparison is not real.

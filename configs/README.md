# Configuration files

Every file here except the HPO spaces is a `RAGConfig` YAML validated by `src/deepfix_rag/config/schema.py`. The HPO files are validated by `src/deepfix_rag/config/hpo.py`.

Run `deepfix schema --json` for the live field list, bounds, and the `index_time_keys` that force a rebuild.

## Start here

Three files are the supported entry points. Everything else is a reference or a template.

| File | What it is | When to use it |
| :--- | :--- | :--- |
| `default.yaml` | The default hybrid baseline: `recursive` chunking at 1024/64, hybrid retrieval, `top_k: 10`, rerank off, `s3://deepfix-index/dev` as the index location. | Your first `deepfix index` and `deepfix eval`. This is also the file the API copies into a fresh workspace as `champion.yaml`, rewritten to point at the workspace corpus and `indexes/current`. |
| `champion.yaml` | A concrete tuned configuration: hybrid, `top_k: 50`, rerank on with `top_n: 5`, `language: german`, a 100-row Bundestag slice. | Start a round from a known-good config, or copy it as the base for your own champion. |
| `hpo_retrieval_small.yaml` | The query-time HPO space for one `hpo-search` sweep: 12 trials, 20 seeded questions, rerank on/off, `top_k` 10/20/50, query expansion 1 to 3, three score-reading fusion modes. | `deepfix hpo --config configs/hpo_retrieval_small.yaml`. Nothing in this space is index-time, so no trial re-indexes. |

A round run through the API does not read this directory: the run bundle carries its own `input/champion.yaml` and `input/hpo_retrieval_small.yaml`. These files are what you copy when running the commands by hand.

## Backend references

Single-purpose configs, kept so each backend and each lever has a checked example in the test suite.

| File | Backend | Notes |
| :--- | :--- | :--- |
| `dense.yaml` | `dense` | Dense-only comparison against `default.yaml`. |
| `rerank.yaml` | `hybrid` | Hybrid with rerank on, `top_k: 15`, `top_n: 5`. |
| `graph.yaml` | `graph` | Memgraph property graph over the `bundestag` ontology. Manual/benchmark only: the promotion gate rejects `graph`, and `compose.graphdb.yml` is optional. |
| `graph_falkordb.yml` | `graph` | The FalkorDB variant of the same. Note the `.yml` suffix. Same restrictions. |
| `graph_hybrid_falkordb.yml` | `graph_hybrid` | **The promotable graph configuration.** Builds the `bundestag` ontology on FalkorDB, merges duplicate entities in Python, and answers from entity-anchored graph retrieval plus dense retrieval, reranked with `min_relevance_score: 0.3`. Needs a FalkorDB host (`docker compose -f compose.graphdb.yml up -d falkordb`). The first build samples 500 documents (`retrieval.graph_build_document_limit`) so it is cheap to review; `<index_dir>/graph_merges.jsonl` lists every entity merge, and `graph_merges` in the `deepfix index --json` payload counts them. |
| `hpo.yaml` | n/a | The wide manual HPO space: chunking strategies, `top_k` ranges, `bm25_k1`, `bm25_b`. `tune_chunking: false` and `retrieval_only: false`, so a trial can score generation metrics. `--tune-chunking` re-indexes per trial, so never use that flag in an API-driven round. |

## Rules of thumb

- The model ids are **not** in these files. `EMBEDDING_MODEL`, `COHERE_MODEL`, `LLM_MODEL`, and `LLM_JUDGE_MODEL` come from the environment; `mutate-config` rejects `embedding.model` and friends.
- Index-time keys: anything under `chunking.*`, plus `retrieval.backend`, `retrieval.vector_store`, `retrieval.bit_width`, `retrieval.graph_store`, `retrieval.graph_extraction_schema`, `retrieval.graph_max_paths_per_chunk`, `retrieval.graph_num_workers`, `retrieval.graph_build_document_limit`, `retrieval.graph_dedup_similarity`, `retrieval.graph_dedup_name_ratio`, `retrieval.graph_dedup_max_edit_distance`, `EMBEDDING_MODEL`, and the corpus hash. Changing one needs `deepfix index` again before you score. `retrieval.graph_query_entity_limit` and `retrieval.graph_traversal_hops` are query-time.
- `retrieval.lancedb_index_dir` is a location, not a hyperparameter. `deepfix index --index-dir` and `deepfix eval --index-dir` override it per command.
- `rerank.top_n` must be at most `retrieval.top_k`, and `eval.cutoff_k` at most `rerank.top_n`, when rerank is enabled. The schema rejects a config that breaks either rule.
- `retrieval.dense_weight` and `retrieval.bm25_weight` are read only under `fusion_mode: relative_score` or `dist_based_score`. The default `reciprocal_rerank` ignores them. Under `backend: graph_hybrid`, `dense_weight` and `graph_weight` always apply: each side is min-max normalised, weighted, and merged.
- `retrieval.query_expansion_num` above 1 calls the generation model: one completion per question per requested count. The shipped small space samples 1 to 3 and the pipeline caches each paraphrase across a study and across a compare, so the cost is bounded. On `dense` or `bm25` the value is inert.
- Generated candidates belong under the run directory (`runs/<run_id>/candidate.yaml`), not in this folder. Committed run artifacts were removed for that reason; `git log` has the history if you need the old benchmark outputs.
- `configs/champion.yaml` in this folder is a template. The champion a deployment actually uses is the file in the API workspace, and `promotion.apply_candidate` is its only writer after seeding.

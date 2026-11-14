# Configuration files

Every file here is a `RAGConfig` YAML validated by `src/deepfix_rag/config/schema.py`. Run `deepfix schema --json` for the live field list, bounds, and the `index_time_keys` that force a rebuild.

## Start here

Three files are the supported entry points. Everything else is a reference or a template.

| File | What it is | When to use it |
| :--- | :--- | :--- |
| `default.yaml` | The default hybrid baseline. | Your first `deepfix index` and `deepfix eval`. This is also the file the API seeds into a fresh workspace as `champion.yaml`. |
| `champion.yaml` | A concrete tuned configuration: hybrid, `top_k=50`, rerank on, `language: german`, 100-row Bundestag slice. | Start a round from a known-good config, or copy it as the base for your own champion. |
| `hpo_retrieval_small.yaml` | The query-time HPO space for one `hpo-search` sweep: 12 trials, 20 seeded questions, rerank on/off, `top_k` 10/20/50, three score-reading fusion modes. | `deepfix hpo --config configs/hpo_retrieval_small.yaml`. Nothing in this space is index-time, so no trial re-indexes. |

A round run through the API does not read this directory: the run bundle carries its own `input/champion.yaml` and `input/hpo_retrieval_small.yaml`. These files are what you copy when running the commands by hand.

## Backend references

Single-purpose configs kept so each backend has a checked example in the test suite.

| File | Backend | Notes |
| :--- | :--- | :--- |
| `dense.yaml` | `dense` | Dense-only comparison against `default.yaml`. |
| `rerank.yaml` | `hybrid` | Hybrid with rerank on and `top_k=15`. |
| `graph.yaml` | `graph` | Memgraph property graph. Manual/benchmark only; the promotion gate rejects `graph`. |
| `graph_falkordb.yml` | `graph` | The FalkorDB variant of the same. Note the `.yml` suffix. |
| `hpo.yaml` | n/a | The wide manual HPO space: chunking strategies, `top_k`, `bm25_k1`, `bm25_b`. `--tune-chunking` re-indexes per trial, so never use it in an API-driven round. |

## Rules of thumb

- The model ids are **not** in these files. `EMBEDDING_MODEL`, `COHERE_MODEL`, `LLM_MODEL`, and `LLM_JUDGE_MODEL` come from `.env`; `mutate-config` rejects `embedding.model` and friends.
- Anything under `chunking.*`, plus `retrieval.backend`, `retrieval.vector_store`, and `retrieval.bit_width`, is index-time: changing it needs `deepfix index` again before you score.
- `retrieval.lancedb_index_dir` is a location, not a hyperparameter. `deepfix index --index-dir` and `deepfix eval --index-dir` override it per command.
- Generated candidates belong under the run directory (`runs/<run_id>/candidate.yaml`), not in this folder. Committed run artifacts were removed for that reason; `git log` has the history if you need the old benchmark outputs.

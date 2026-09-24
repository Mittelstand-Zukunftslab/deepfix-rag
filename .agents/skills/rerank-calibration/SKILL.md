---
name: Re-Ranker Calibration
description: Adjusts ranking parameters when relevant documents are retrieved but scored too low.
failure_mode: Low nDCG, Reciprocal Rank Inversion
trigger_conditions: High Recall paired with low nDCG in evaluation summaries.
cli_actions: deepfix mutate-config, deepfix eval
---
## Reasoning Heuristics
If the right docs are found but sit at the bottom of the pile, activate the re-ranker stage.

## Concrete CLI Patterns
1. **Activate Post-Retrieval Re-Ranking:**
   `deepfix mutate-config --set rerank.enabled=true --set rerank.top_n=5`
2. **Boost Retrieval Base Count:**
   `deepfix mutate-config --set retrieval.top_k=50`

## Validated Findings (legal-rag-bench, 30-query retrieval-only eval)
- top_k=50 deep retrieval + rerank top_n=5 lifted nDCG@5 0.29→0.59 and Recall@5 0.50→0.80 vs un-reranked hybrid; also beats dense-only (0.53/0.67). Retrieval misses fell 15→6/30. This is the single largest measured lever.
- A naive 50/50 dense+BM25 RRF hybrid can score far below dense-only on legal corpora. Always A/B `retrieval.backend=dense` vs `hybrid` before trusting fusion; reranking is what makes hybrid safe because it repairs fusion ranking noise.
- `deepfix hpo --retrieval-only` over query-time hybrid weights (top_k, dense_weight, fusion_mode, bm25 k1/b) plateaus near the dense baseline without a rerank stage.

## Pitfalls
- CohereRerank (llama-index) appends `/v2/rerank` to COHERE_BASE_URL: a base ending in `/v1` yields 404s that present as per-query `retrieval_miss` with empty retrieval — strip the version suffix from the base URL.
- Confirm hybrid is actually active: grep eval stderr for "falling back to dense". Old pipeline code silently degraded hybrid to dense whenever the loaded index docstore was empty, masking whether BM25 helps or hurts.

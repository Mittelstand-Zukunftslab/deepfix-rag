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

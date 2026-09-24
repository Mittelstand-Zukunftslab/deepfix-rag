---
name: Chunking Tuner
description: Optimizes document slice sizes and boundaries.
failure_mode: Low Recall, Semantic Discontinuity, Context Fragmentation
trigger_conditions: Diagnosed poor chunk mappings or low recall in PyTerrier eval.
cli_actions: deepfix hpo, deepfix mutate-config
---
## Reasoning Heuristics
If the diagnostic report indicates chunks are too small or split mid-concept, the LLM loses context. Consider shifting from recursive chunking to semantic chunking, or optimizing chunk sizes using HPO.

## Concrete CLI Patterns
1. **Execute HPO Chunk Tuning:**
   `deepfix hpo --tune-chunking`
2. **Mutate Config to Semantic Chunking:**
   `deepfix mutate-config --set chunking.strategy=semantic --output new.yaml`

---
name: Query Adaptation & Rewriting
description: Adapts backend retrieval strategies when user queries lack exact vocabulary.
failure_mode: Out-of-Vocabulary (OOV), Lexical Mismatch
trigger_conditions: BM25 scores low but dense retrieval is adequate, indicating missing keywords.
cli_actions: deepfix mutate-config
---
## Reasoning Heuristics
When queries lack exact language, shift the backend to hybrid or dense, and adjust fusion weights.

## Concrete CLI Patterns
1. **Enable Hybrid Search Backend:**
   `deepfix mutate-config --set retrieval.backend=hybrid`
2. **Adjust Dense Weight Factor:**
   `deepfix mutate-config --set retrieval.dense_weight=0.7`

---
name: Embedding Optimization
description: Improves vector similarity metrics through dimensionality reduction.
failure_mode: Vector Noise, Poor Semantic Similarity
trigger_conditions: High dimensionality causing noise, high latency, or low context precision.
cli_actions: deepfix mutate-config
---
## Reasoning Heuristics
High-dimensional embeddings can cause the 'curse of dimensionality'. Enable PCA to compress features.

## Concrete CLI Patterns
1. **Initialize PCA:**
   `deepfix mutate-config --set data.enable_embedding_pca=true`
2. **Set PCA Components:**
   `deepfix mutate-config --set data.embedding_pca_components=256`

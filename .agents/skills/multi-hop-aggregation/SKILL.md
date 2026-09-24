---
name: Multi-Hop Aggregation Tuning
description: Improves complex queries requiring information joined across multiple documents.
failure_mode: Multi-Hop Reasoning Failures, Incomplete Context
trigger_conditions: Questions requiring synthesis score low on Contextual Recall.
cli_actions: deepfix mutate-config, deepfix hpo
---
## Reasoning Heuristics
Increase graph traversal hops if the system needs to link multiple disconnected concepts together.

## Concrete CLI Patterns
1. **Expand Graph Traversal Radius:**
   `deepfix mutate-config --set retrieval.graph_traversal_hops=3`
2. **Optimize Graph Backend Parameters:**
   `deepfix hpo --paradigm graph`

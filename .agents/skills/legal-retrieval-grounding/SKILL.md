---
name: Legal Retrieval Grounding
description: Forces strict relational graph extraction for legal domain texts.
failure_mode: Lack of Entity Precision, Failed Citations
trigger_conditions: Running Legal RAG Bench where references to specific articles are required.
cli_actions: deepfix mutate-config, deepfix hpo
---
## Reasoning Heuristics
Standard vector similarity often confuses Legal Statutes. Use Graph RAG to forcefully map Entities (COURT to LAW).

## Concrete CLI Patterns
1. **Switch to Property Graph Backend:**
   `deepfix mutate-config --set retrieval.backend=graph`
2. **Enforce Legal Extraction Schema:**
   `deepfix mutate-config --set retrieval.graph_extraction_schema=domain_legal`

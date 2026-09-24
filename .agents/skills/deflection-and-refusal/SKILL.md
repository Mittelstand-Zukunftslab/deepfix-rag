---
name: Deflection & Refusal Mitigation
description: Reduces LLM hallucination and strict failure to answer.
failure_mode: High Deflection Rate, Prompt Misalignment
trigger_conditions: Retrieval metrics are strong but Generation metrics (Answer Relevancy) are failing.
cli_actions: deepfix mutate-config
---
## Reasoning Heuristics
If documents are retrieved perfectly but the LLM refuses to answer, tuning the generation prompt or synthesizer type is required.

## Concrete CLI Patterns
1. **Change Response Synthesizer Algorithm:**
   `deepfix mutate-config --set generation.response_synthesizer=compact_and_refine`
2. **Lower Sampling Temperature:**
   `deepfix mutate-config --set generation.temperature=0.2`

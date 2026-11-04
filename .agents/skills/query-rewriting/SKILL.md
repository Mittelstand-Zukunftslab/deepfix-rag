---
name: query-rewriting
description: >
  Alias of hybrid-fusion-tuning. Use when a hybrid champion already has high
  recall and the remaining misses look like a vocabulary mismatch. This skill
  tunes fusion weights. It does not rewrite queries. Follow hybrid-fusion-tuning.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "Alias of hybrid-fusion-tuning (optimization-loop routing row 5)"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.fusion_mode=relative_score --set retrieval.dense_weight=0.7 --set retrieval.bm25_weight=0.3 --json"
---

This skill was renamed to `hybrid-fusion-tuning`. Read `.agents/skills/hybrid-fusion-tuning/SKILL.md` and follow that procedure. The old name remains a plugin alias. Query expansion and HyDE are not available.

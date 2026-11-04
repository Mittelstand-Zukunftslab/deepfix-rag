---
name: embedding-selection
description: >
  Alias of retrieval-backend-selection. Use when retrieval_miss persists and
  dense, bm25, and hybrid have not been compared. The embedding model is not a
  config key. Follow retrieval-backend-selection; this folder remains so older
  prompts still resolve.
failure_mode:
  - retrieval_miss
trigger_conditions:
  - "Alias of retrieval-backend-selection (optimization-loop routing row 4)"
cli_actions:
  - "deepfix mutate-config --base configs/champion.yaml --output <run_dir>/candidate.yaml --set retrieval.backend=dense --json"
---

This skill was renamed to `retrieval-backend-selection`. Read `.agents/skills/retrieval-backend-selection/SKILL.md` and follow that procedure. The old name remains a plugin alias. It does not change `EMBEDDING_MODEL`.

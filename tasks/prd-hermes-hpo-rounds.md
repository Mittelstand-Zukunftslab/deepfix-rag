# PRD: Hermes HPO Rounds

## Introduction

DeepFix already has an Optuna-backed `deepfix hpo` command (`src/deepfix_rag/cli/hpo.py`, engine in `src/deepfix_rag/hpo.py`, schemas in `src/deepfix_rag/config/hpo.py`). The Hermes optimization sandbox, however, is contracted to **not** use it. `.agents/skills/optimization-loop/SKILL.md` and the run prompt in `src/deepfix_rag/hermes_client.py` (`optimization_instructions`) tell the agent to try **one** hand-written `mutate-config` hypothesis per round. `tasks/prd-hermes-optimizer.md` lists “turning `deepfix hpo` on inside a round” as a non-goal, and US-006 requires the skill to keep that ban.

That contract failed on the Bundestag 500-document slice (`bt500-20261112`, 80 dev questions, baseline hybrid / `top_k` 10 / rerank off: nDCG@5 0.667, Recall@5 0.975). Hermes spent its 40 turns on manual candidates, never measured rerank (the obvious lever: high recall, weaker nDCG), and hit the turn cap before writing `result.json`. Dense/BM25 weights and `bm25_b` did not move scores under the default fusion mode. Each full 80-question eval took about 10 minutes, pushed the Hermes container to 3.5–3.9 GB of its 4 GB limit, and re-downloaded the 154 MB index from S3. Two blockers also stopped rerank and even some retrieval-only evals: Hermes’s terminal strips `LLM_MODEL`, `OPENROUTER_API_KEY`, and `COHERE_API_KEY` from commands, and since commit `879d640` hybrid retrieval still calls `require_model("LLM_MODEL")` even with `--retrieval-only` and `query_expansion_num=1`; `COHERE_BASE_URL` defaults to OpenRouter’s chat base (`https://openrouter.ai/api/v1` in `src/deepfix_rag/config/settings.py` and `example.env`), so LlamaIndex `CohereRerank` posts to `/api/v1/v2/rerank` and gets a 404.

This feature teaches Hermes to run **one** bounded `deepfix hpo` call over a **small query-time search space**, then confirm the best trial on the full dev set against the existing promotion gate. The API still owns holdout, the 0.025 primary-delta rule, and promotion. Hermes still recommends at most one candidate in `result.json` and never writes `configs/champion.yaml`. The 40-turn cap (`agent.max_turns` in `docker/hermes/sandbox_boot.py`, `HERMES_MAX_TURNS` in `src/deepfix_rag/hermes_client.py`) stays 40. Chunking remains a separate one-hypothesis, re-index path. `bm25_k1` and `bm25_b` stay out of the v1 space.

This document is the plan. It does not implement the feature. It is written for review only and is not a commit to the repository.

There is no PRD-authoring skill under this repo’s `.agents/skills/` (those folders are optimization pathology skills). The format follows `tasks/prd-hermes-optimizer.md`.

### Clarifying questions, answered from the owner's decisions

The planning skill asks these before a PRD is written. The owner already answered them:

1. How should rerank be called?
   A. OpenRouter’s rerank API, model `cohere/rerank-4-fast`, with a DeepFix-owned client and base URL, and no Cohere API key.
   B. The official Cohere API (`https://api.cohere.com` + a Cohere key).
   Answer: **A.**

2. What is the HPO trial budget?
   A. 12 trials on 20 seeded questions, then one confirmation eval on the full dev set.
   B. A larger or smaller trial count, or trials on the full dev set.
   Answer: **A.**

3. Which parameters belong in the v1 search space?
   A. Rerank on/off, `rerank.top_n`, `retrieval.top_k`, fusion mode, and dense/BM25 weights only when the fusion mode actually reads them. Leave `bm25_k1` / `bm25_b` out. Leave chunking as a separate manual hypothesis.
   B. Include BM25 saturation parameters and/or chunking in the same sweep.
   Answer: **A.**

4. What happens to the 40-turn cap and the promotion gate?
   A. Keep `agent.max_turns` at 40. The confirmation eval must still clear the existing gate (`OPTIMIZATION_MIN_PRIMARY_DELTA` 0.025 on the full dev set, secondary tolerance, then the API’s holdout check). HPO scores on the 20-question subsample are not enough to recommend.
   B. Raise the turn cap, or let HPO’s subsample scores replace the full-dev gate.
   Answer: **A.**

5. Where is the index cached so trials do not re-download S3?
   A. Under the run directory only (default). Do not keep a cross-run cache on the Hermes volume.
   B. On the Hermes volume so later runs can reuse it.
   Answer: **A.**

## Amendments required in `tasks/prd-hermes-optimizer.md`

Do **not** edit that file as part of writing this document. After this PRD is accepted and added under `tasks/`, a follow-up edit should:

1. **Non-Goals** (current bullet: “Implementing `deepfix export` (it still exits 2 with "export is not implemented yet") or turning `deepfix hpo` on inside a round.”). Split it. Keep the `deepfix export` sentence. Remove “or turning `deepfix hpo` on inside a round.” Add a pointer: HPO inside an API-driven round is specified in `tasks/prd-hermes-hpo-rounds.md`.
2. **US-006 acceptance criterion** (current text: “`deepfix hpo` and `deepfix run-diagnostic` stay on `PATH` because they are part of the CLI, and the skill still tells the agent not to call them during a round.”). Keep both commands on `PATH`. Keep the ban on `deepfix run-diagnostic`. Replace the HPO ban with: the agent may call `deepfix hpo` during a round only as specified in `tasks/prd-hermes-hpo-rounds.md` and the `hpo-search` skill.
3. **US-006 verification note** (the sentence “The loop skill and the prompt still tell the agent not to call `deepfix hpo` or `deepfix run-diagnostic` during a round.”). Update it the same way once HPO-006 lands. Do not reopen or uncheck US-001 through US-010.

## Goals

- Make a retrieval-only hybrid (or dense/BM25) eval work inside Hermes without `LLM_MODEL` when `retrieval.query_expansion_num` is 1, so the agent does not have to smuggle model ids out of `/run/s6/container_environment`.
- Call OpenRouter rerank at the documented endpoint with `OPENROUTER_API_KEY` and model `cohere/rerank-4-fast`, so turning `rerank.enabled` on no longer 404s.
- Keep Hermes’s terminal secret scrub. Inject non-secret model ids and the OpenRouter key into the `deepfix` child through the existing shim, never via command-line assignments and never by reading the s6 env dir from the agent.
- Give `deepfix hpo` a small shipped search space, `--seed`, `--timeout`, a file-backed study URI, a local index cache under the run directory, and incremental artifacts, so 12 trials stay inside the 4 GB memory limit and a mid-run timeout still leaves a best-so-far result.
- Change the optimization-loop skill and the Hermes run prompt so a query-time round is: baseline smoke → one HPO call → full-dev confirmation → `deepfix gate` → `result.json`. Fit that path in about 10–15 turns under the existing 40-turn cap.
- Leave promotion, holdout, the 0.025 gate, Auto Deploy, and the 40-turn cap unchanged. After the code stories land, rerun the 500-document Bundestag slice and report the best config; do not promote it.

## User Stories

Stories are ordered below. Each of HPO-001 through HPO-006 is one implementation session on `feat/hermes-optimizer`. HPO-007 is an operator validation session after the code has been deployed; cloud agents have no server access. This PRD does not check them off.

### HPO-001: Lazy LLM for retrieval-only hybrid

**Description:** As the optimization agent, I want a `--retrieval-only` hybrid eval with `query_expansion_num=1` to run without `LLM_MODEL`, so a stripped or empty model id does not fail every question.

**Acceptance Criteria:**

- [ ] In `src/deepfix_rag/pipeline.py`, `_build_retriever` does not call `_get_llm()` (and therefore does not call `require_model("LLM_MODEL")`) when `retrieval.query_expansion_num <= 1`. `QueryFusionRetriever` is constructed with `llm=None` (or omitted) and `num_queries` equal to the configured value. Generation paths and `query_expansion_num > 1` still require `LLM_MODEL`.
- [ ] A retrieval-only hybrid eval against the existing fixture index and `tests/data/sample_qa.jsonl` (or the equivalent sample QA used by `tests/test_hpo.py`) succeeds with `LLM_MODEL` unset and `LLM_API_KEY` unset. `--retrieval-only` still does not require `LLM_JUDGE_MODEL` (already true in `src/deepfix_rag/evaluation/runner.py`).
- [ ] A hybrid eval with `query_expansion_num=2` and `LLM_MODEL` unset fails by naming `LLM_MODEL` (`ModelNotConfigured` / `error_type` `missing_model`).
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.
- [ ] `uv run --env-file .env pytest -m "not network and not e2e"` stays green on the tests this story adds or touches (and does not regress the known `test_incremental_index` failure if that is still open).

### HPO-002: OpenRouter rerank client

**Description:** As the evaluation pipeline, I want rerank to call OpenRouter’s rerank API with our OpenRouter key and model `cohere/rerank-4-fast`, so a candidate with `rerank.enabled=true` is scored instead of 404ing.

**Acceptance Criteria:**

- [ ] `RAGWorkflow._get_reranker` in `src/deepfix_rag/pipeline.py` no longer uses LlamaIndex `CohereRerank` against `settings.COHERE_BASE_URL` in a way that appends `/v2/rerank`. A DeepFix-owned client posts to OpenRouter’s rerank endpoint and reorders the retrieved nodes.
- [ ] The request matches the current OpenRouter docs, fetched for this PRD on 13 November 2026 (Africa/Niamey) from [Submit a rerank request](https://openrouter.ai/docs/api/api-reference/rerank/submit-a-rerank-request):
  - `POST https://openrouter.ai/api/v1/rerank` (when the configured OpenAI-compatible base is `https://openrouter.ai/api/v1`, the client appends `/rerank` only, never `/v2/rerank`).
  - Headers: `Authorization: Bearer <OPENROUTER_API_KEY>`, `Content-Type: application/json`.
  - JSON body, required fields: `documents` (non-empty array of strings, or objects with `text` and/or `image`), `model`, `query`. Optional: `top_n` (integer >= 1), plus the documented optional `provider`, `session_id`, `trace`, and `user` fields, which this client may omit.
  - `model` is `require_model("COHERE_MODEL")`. Production and `example.env` keep `cohere/rerank-4-fast`.
  - Auth is `OPENROUTER_API_KEY` (or the same OpenRouter key already injected for model calls). The client does not require `COHERE_API_KEY`.
  - Response shape used for ranking: `results[]` with `index` (original document offset) and `relevance_score`, optionally `document.text`. Results are sorted by relevance in the documented example.
- [ ] The default `COHERE_BASE_URL` in `src/deepfix_rag/config/settings.py` and the comment in `example.env` no longer describe a Cohere `/v2` base. If the setting is left at `https://openrouter.ai/api/v1`, the new client must still hit `/api/v1/rerank`. A unit test against a local HTTP server (no `unittest.mock` of domain code, consistent with `AGENTS.md` and `tasks/prd-hermes-optimizer.md`) asserts method `POST`, path `/api/v1/rerank` (not `/api/v1/v2/rerank` and not `/v2/rerank`), the JSON keys `documents`, `model`, `query`, and `top_n` when rerank `top_n` is set, and a `Bearer` authorization header. The test server returns a documented-shaped `results` list; the client’s ranked order follows `index` / `relevance_score`.
- [ ] If the OpenRouter docs page has changed when the story is implemented, the implementer re-fetches that URL and updates the client and this test to the live contract, noting the change in the commit message.
- [ ] `.agents/skills/rerank-calibration/SKILL.md` pitfall about `/v2/rerank` is updated to describe the OpenRouter `/api/v1/rerank` client. Native Cohere is out of scope.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.
- [ ] `uv run --env-file .env pytest -m "not network and not e2e"` includes the new local-HTTP rerank tests.

### HPO-003: Shim injects eval env without bypassing the scrub

**Description:** As the operator, I want `deepfix eval` and `deepfix hpo` inside Hermes to see model ids and the OpenRouter key without putting those values on the command line, so the terminal scrub can stay in place and the s6 env-dir workaround can be retired.

**Acceptance Criteria:**

- [ ] Hermes’s terminal secret scrub is not disabled or circumvented. The agent is not told to read `/run/s6/container_environment` or `/var/run/s6/container_environment`.
- [ ] `docker/hermes/sandbox_boot.py` writes non-secret eval settings (`LLM_MODEL`, `LLM_JUDGE_MODEL`, `LLM_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`, `COHERE_MODEL`, `COHERE_BASE_URL`) into a root-owned file the shim can read. It also writes the OpenRouter key into the existing sealed `0600` secrets path used at boot (today `/opt/data/.env` already receives `OPENROUTER_API_KEY`). `LLM_API_KEY` stays out of the Hermes container environment, as in `compose.hermes.yml`.
- [ ] `src/deepfix_rag/deepfix_shim.py` re-injects those model ids and, when the child would otherwise lack a key for OpenRouter calls, maps `OPENROUTER_API_KEY` into the LLM / embedding / rerank key slots the pipeline reads. The child’s argv never contains key values. The shim’s existing heartbeat and log redaction (`API_SERVER_KEY`, `OPENROUTER_API_KEY`, S3 secrets, `LLM_API_KEY`) still apply.
- [ ] A test starts the shim (or a thin wrapper around the injection function) with the sensitive variables removed from the environment, as the terminal scrub does, and asserts the child process sees the restored model ids and OpenRouter key, that argv has no secret substrings, and that the injection path does not read the s6 env directories.
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.
- [ ] `uv run --env-file .env pytest -m "not network and not e2e"` includes the new shim tests.

### HPO-004: Cheaper HPO trials and resumable artifacts

**Description:** As the optimization agent, I want one `deepfix hpo` process to seed a 20-question subsample, reuse a local copy of the index, stop on a time budget, and write best-so-far files after every trial, so 12 trials fit in memory and a timeout still leaves a result.

**Acceptance Criteria:**

- [ ] `deepfix hpo` gains `--seed` and `--timeout`. `--seed` is passed through to each trial’s `EvaluationRunner.run` (same semantics as `deepfix eval --seed` in `src/deepfix_rag/cli/eval.py`: a deterministic sample of `--limit` rows, original order). `--timeout` is passed to `HyperparameterOpt.optimize` (the engine already accepts `timeout`; the CLI in `src/deepfix_rag/cli/hpo.py` currently does not).
- [ ] The default trial subsample for the shipped small-space workflow is `--limit 20 --seed 0`. The CLI still allows other values.
- [ ] When the base config’s `retrieval.lancedb_index_dir` is an `s3://` URI (or another remote path), HPO copies the index once into `<run_dir>/idx-cache` (or an explicit `--index-dir` under the run directory) and points every trial at that local path. The default cache location is **under the run directory only**. A cross-run cache on the Hermes volume is out of scope. After the first copy, later trials in the same process must not re-download the object store.
- [ ] `--storage-uri` continues to work. The skill and the shipped YAML default the study to `sqlite:///<run_dir>/hpo.db` rather than `sqlite:///:memory:`. `load_if_exists=True` already on `optuna.create_study` in `src/deepfix_rag/hpo.py` then resumes that file after a process restart.
- [ ] After each completed trial, HPO writes `<run_dir>/hpo_trials.json` (trial number, params, metric values, state) and refreshes a best-so-far YAML at the `--output` path when a best trial exists. A `--timeout` or crash after at least one complete trial leaves those files behind.
- [ ] Each trial still logs to the existing MLflow experiment `MLFLOW_DEV_RAG_EXPERIMENT`. If `HPOResult.log_to_mlflow` is wired from the CLI, it is nested or clearly named and still must not log secrets (FR-16 in the Hermes PRD).
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.
- [ ] Tests cover: `--seed` stability on a fixture; `--timeout` stopping the study; a file `storage_uri` resuming with `load_if_exists`; the local cache path being used for a fake `s3://` URI in a tmp run dir (no live S3 required). `uv run --env-file .env pytest -m "not network and not e2e"` includes them. `deepfix hpo --help` lists `--seed` and `--timeout`.

### HPO-005: Small retrieval search space

**Description:** As an operator, I want a checked-in HPO YAML whose hybrid space only samples parameters that change ranking, including rerank, so twelve trials are not wasted on no-ops or re-indexing.

**Acceptance Criteria:**

- [ ] `HPOSearchSpace` / `HybridSearchSpace` in `src/deepfix_rag/config/hpo.py` can sample `rerank.enabled`, and when enabled `rerank.top_n` and a widened `retrieval.top_k`, subject to the existing `RAGConfig` validator in `src/deepfix_rag/config/schema.py` (`rerank.top_n <= retrieval.top_k` and `eval.cutoff_k <= rerank.top_n`). Invalid combinations are not suggested, or they are pruned before eval.
- [ ] `retrieval.dense_weight` (and the complementary `bm25_weight`) are suggested **only** when `retrieval.fusion_mode` is `relative_score` or `dist_based_score`. They are not sampled under `reciprocal_rerank` or `simple` (documented no-ops in `.agents/skills/hybrid-fusion-tuning/SKILL.md`).
- [ ] A shipped file `configs/hpo_retrieval_small.yaml` sets `retrieval_only: true`, `tune_chunking: false`, `n_trials: 12`, `limit: 20`, `query_expansion_num_min` and `query_expansion_num_max` of 1, and the following hybrid space:
  - `rerank.enabled`: false, true
  - `rerank.top_n`: 5, 10 (only if rerank is on)
  - `retrieval.top_k`: 10, 20, 50
  - `retrieval.fusion_mode`: `reciprocal_rerank`, `relative_score`, `dist_based_score` (not `simple`)
  - `retrieval.dense_weight`: 0.3, 0.5, 0.7, only under score-based fusion
- [ ] The shipped space does **not** include `bm25_k1`, `bm25_b`, `query_expansion_num` other than 1, or any `chunking.*` keys. `configs/hpo.yaml` may keep its broader manual-use bounds, but the agent is pointed at the small file.
- [ ] Tests assert the small space never emits expansion > 1, never emits chunking keys, never emits `bm25_k1` / `bm25_b`, never emits weights under `reciprocal_rerank`, and that enabled rerank always satisfies the window constraints. `uv run ruff check` and `uv run ruff format --check` pass. `uv run --env-file .env pytest -m "not network and not e2e"` includes these tests.

### HPO-006: Skills and prompt use one HPO call

**Description:** As the optimization agent, I want the entry skill and the run prompt to tell me to run one small HPO sweep and then a full-dev confirmation, so I can finish under 40 turns and still write `result.json`.

**Acceptance Criteria:**

- [ ] A new read-only skill `.agents/skills/hpo-search/SKILL.md` is the procedure for query-time search (rerank and/or fusion). It is installed the same way as the other skills (root-owned, mode `0444`, listed by `GET /v1/skills`).
- [ ] `.agents/skills/optimization-loop/SKILL.md` drops the hard rule “No `deepfix hpo`”. It still forbids `run-diagnostic`, promotion, holdout, and editing `configs/champion.yaml`. For routing rows that today go to `rerank-calibration` or `hybrid-fusion-tuning` (high recall / low nDCG, or hybrid fusion with remaining misses), the loop tells the agent to follow `hpo-search` instead of a single `mutate-config`. `chunking-tuning` and `retrieval-backend-selection` stay one-hypothesis paths (no HPO, re-index when required). The 40-turn budget line is unchanged.
- [ ] The documented workflow, designed to fit in about 10–15 turns, is:
  1. `skill_view` on `optimization-loop` and `hpo-search`.
  2. Baseline smoke: `deepfix eval --config <champion> --dataset <dev_path> --retrieval-only --limit 30 --seed 0 --json`. On errors, write `result.json` with a null candidate, `status: blocked`, and the error in `summary`, then finish.
  3. If the champion index is remote, copy it once into `<run_dir>/idx-cache` (HPO-004).
  4. One command: `deepfix hpo --config configs/hpo_retrieval_small.yaml --retrieval-only --limit 20 --seed 0 --n-trials 12 --timeout <budget> --storage-uri sqlite:///<run_dir>/hpo.db --output <run_dir>/candidate.yaml --json` (champion as `base_config`, local index dir applied). The `deepfix` shim keeps `output/heartbeat.json` fresh for the whole call.
  5. After HPO starts producing artifacts, a provisional `result.json` with the required keys exists (best-so-far path or null). A turn-cap wrap-up (`iteration_budget_exhausted`) must still find a valid file.
  6. Confirmation, no `--limit`: `deepfix eval --config <run_dir>/candidate.yaml --compare <champion> --dataset <dev_path> --retrieval-only --json` then `deepfix gate --compare … --primary ndcg --json`. Recommend only when `pass` is true under the existing thresholds (`OPTIMIZATION_MIN_PRIMARY_DELTA` default 0.025, `OPTIMIZATION_SECONDARY_TOLERANCE` default -0.01). Subsample HPO scores must not be used for the gate.
  7. Finalize `result.json` (`candidate_config_path`, `primary_metric`, `summary`, `mlflow_run_id`), print it, finish. Do not promote.
- [ ] `optimization_instructions` in `src/deepfix_rag/hermes_client.py` is updated to match: it must not say “Do not run `deepfix hpo`”; it must tell the agent to follow `hpo-search` for query-time work, to run HPO once rather than hand-trying candidates, to write `result.json` early, and to leave `deepfix run-diagnostic` forbidden. `HERMES_MAX_TURNS` remains 40. “Test one hypothesis in this run” is reworded so HPO may search many configs internally and still recommend at most one candidate.
- [ ] Manual HPO sections in `.agents/skills/chunking-tuning/SKILL.md` and `.agents/skills/hybrid-fusion-tuning/SKILL.md` are updated so they do not contradict the new loop (chunking HPO stays out of API rounds; fusion/rerank in a round go through `hpo-search`).
- [ ] `tests/test_skills.py` (and prompt tests in `tests/test_hermes_client.py` if that is where instructions are asserted) check that the loop and prompt mention `deepfix hpo` / `hpo-search` as the query-time path, still forbid `run-diagnostic` and promotion, still require `result.json`, and still say the turn budget is 40. `uv run ruff check` and `uv run ruff format --check` pass. `uv run --env-file .env pytest -m "not network and not e2e"` includes these tests.

### HPO-007: Bundestag 500-document rerun (report only)

**Description:** As the operator, I want the same 500-document Bundestag slice rerun with the new workflow after HPO-001 through HPO-006 are deployed, so we learn whether rerank or another small-space trial beats the baseline — without promoting anything to live.

**Acceptance Criteria:**

- [ ] This story is executed on the server by the operator (or the assistant that already operates Dokploy). It is not a cloud-agent code change and it does not push to `dev` or `main`.
- [ ] Preconditions: HPO-001–006 are on `feat/hermes-optimizer` and the Hermes image used for the run includes them. Auto Deploy stays off. One service is rebuilt at a time if a rebuild is needed, with approval. The live champion is not replaced.
- [ ] The run starts from the existing best configuration (hybrid, `top_k` 10, rerank off). Corpus size is the same ~500-document slice and the matching 80 dev questions used in `bt500-20261112`. Index prefix stays under `s3://deepfix-index/optimization/runs/…`. The HPO cache, if used, stays under that run directory.
- [ ] Hermes follows the HPO-006 workflow: baseline smoke, one 12×20 HPO call, full-dev confirmation, gate, `result.json`. Nothing is promoted, even if the gate passes.
- [ ] The write-up to the owner reports: baseline nDCG@5 and Recall@5; whether HPO completed all 12 trials or stopped early; the best trial’s params and subsample scores; the full-dev candidate and baseline scores and the gate decision (`pass` / deltas / CI); peak Hermes memory versus the 4 GB limit; whether rerank was actually measured (no 404s, no stripped-key failures); and the path to `result.json` / `hpo_trials.json`. Secrets are not printed.

## Functional Requirements

- FR-1: A retrieval-only hybrid (and dense/BM25) evaluation with `retrieval.query_expansion_num <= 1` must not require `LLM_MODEL` or `LLM_API_KEY`. Query expansion above 1 and any generation metric path must still fail by naming the missing variable.
- FR-2: When `rerank.enabled` is true, the pipeline must `POST` to OpenRouter `https://openrouter.ai/api/v1/rerank` (or the same path on a configured OpenRouter-compatible base) with `documents`, `model`, `query`, and `top_n` as documented, authorized by `OPENROUTER_API_KEY`. It must not call Cohere’s `/v2/rerank` path and must not require `COHERE_API_KEY`. The model id is `COHERE_MODEL` (default / configured `cohere/rerank-4-fast`).
- FR-3: The Hermes terminal secret scrub must remain enabled. Model ids and the OpenRouter key used by `deepfix` children must come from the shim / boot-time files, not from command-line `VAR=value` prefixes and not from the agent reading s6’s `container_environment`.
- FR-4: `deepfix hpo` must support `--retrieval-only`, `--limit`, `--seed`, `--timeout`, `--n-trials`, `--storage-uri`, `--output`, and `--json`. The shipped agent workflow is 12 trials, 20 questions, seed 0, file-backed SQLite under the run directory, and a timeout the operator or skill can set.
- FR-5: HPO trials must reuse a local index copy under the run directory when the champion index is remote. They must not re-download the S3 index on every trial. They must not write a default cache outside that run directory.
- FR-6: The v1 agent search space is exactly the small space in HPO-005. Weights are sampled only for fusion modes that read them. `bm25_k1`, `bm25_b`, query expansion above 1, and chunking are absent.
- FR-7: An API-driven Hermes round may run `deepfix hpo` once for query-time search, then must confirm the best trial on the **full** dev set and apply `deepfix gate` with the existing thresholds. Subsample metrics must not be used to recommend. Hermes still writes at most one `candidate_config_path` (or null) in `result.json`.
- FR-8: `result.json` must exist before the 40-turn budget is exhausted if HPO has already produced a best-so-far or the baseline has failed. The required keys stay `candidate_config_path`, `primary_metric`, `summary`, and `mlflow_run_id`.
- FR-9: Hermes must not promote, must not receive holdout, and must not write `configs/champion.yaml` or `indexes/current`. The API remains the only promoter. `OPTIMIZATION_MIN_PRIMARY_DELTA` (0.025), `OPTIMIZATION_SECONDARY_TOLERANCE` (-0.01), holdout rules, and `agent.max_turns` (40) must not change.
- FR-10: Secrets listed in the Hermes PRD FR-16 (`OPENROUTER_API_KEY`, `API_SERVER_KEY`, S3 secrets, `LLM_API_KEY`) must not appear in prompts, `result.json`, HPO artifacts, MLflow params, or container command lines.
- FR-11: `deepfix run-diagnostic` remains forbidden in a round. Chunking changes in a round remain a single `mutate-config` plus re-index, not HPO.
- FR-12: The `deepfix` shim must keep rewriting `output/heartbeat.json` every 30 seconds for the duration of `deepfix hpo`, as already required by the Hermes PRD FR-18.

## Non-Goals

- Raising `agent.max_turns` / `HERMES_MAX_TURNS` above 40.
- Letting subsample HPO scores replace the full-dev gate or the API holdout check.
- Promoting any Bundestag (or other) candidate from HPO-007, or changing the live champion as part of this work.
- Including `bm25_k1`, `bm25_b`, or `chunking.*` in the v1 agent search space, or running `--tune-chunking` inside an API-driven round.
- Native Cohere rerank (`https://api.cohere.com`, `COHERE_API_KEY`) and any client that appends `/v2/rerank` to OpenRouter’s `/api/v1` base.
- Caching the index on the Hermes volume or any path outside the run directory, as a default.
- Turning the Hermes dashboard on, publishing Hermes ports, mounting `api_data` / `rustfs_data` into Hermes, or changing Auto Deploy.
- Implementing `deepfix export`, removing OpenHands (still US-010 of the Hermes PRD), or changing allowed backends, the sha256 holdout split, or gate thresholds.
- Teaching the agent to read `/run/s6/container_environment` or disabling the terminal scrub.

## Technical Considerations

### What the code does today

- `HyperparameterOpt.optimize` already accepts `n_trials`, `timeout`, `limit`, `retrieval_only` (via metric targets), and `load_if_exists` on a storage URI. The CLI does not expose `--timeout` or `--seed`, and `_evaluate_trial` does not pass `seed`. Default storage in `configs/hpo.yaml` is `sqlite:///:memory:`.
- The hybrid search space samples `top_k`, `query_expansion_num` (1–5), `bm25_k1`, `bm25_b`, weights, and `fusion_mode`. It does not sample `rerank.*`.
- `RAGWorkflow._build_retriever` always passes `llm=self._get_llm()` into `QueryFusionRetriever` for `hybrid` and `mixed`. `_get_llm` calls `require_model("LLM_MODEL")`. LlamaIndex only needs that LLM when `num_queries > 1`.
- `_get_reranker` builds `CohereRerank(base_url=settings.COHERE_BASE_URL, api_key=settings.COHERE_API_KEY)`. `COHERE_BASE_URL` defaults to `https://openrouter.ai/api/v1`. The Cohere client appends `/v2/rerank`.
- `compose.hermes.yml` passes `LLM_MODEL`, `COHERE_*`, and `OPENROUTER_API_KEY`, and deliberately omits `LLM_API_KEY`. `sandbox_boot.py` writes `OPENROUTER_API_KEY` into the profile `.env` but does not write `LLM_MODEL` or `COHERE_MODEL`. The `deepfix` shim redacts secrets in logs; it does not currently restore stripped env vars.
- Each HPO trial constructs a new `EvaluationRunner` and runs `workflow.run(ingest=True)`, which re-opens the configured LanceDB URI. A remote URI is fetched again every trial.
- Skills and `optimization_instructions` explicitly forbid `deepfix hpo` in a round. Manual-only recipes live in the chunking and hybrid-fusion skills.

### OpenRouter rerank contract (verified)

Source: [Submit a rerank request](https://openrouter.ai/docs/api/api-reference/rerank/submit-a-rerank-request), fetched 13 November 2026 (Africa/Niamey).

`POST /api/v1/rerank` on `https://openrouter.ai`, `Authorization: Bearer <token>`, `Content-Type: application/json`. Body: `documents` (min length 1; strings or `{text, image}` objects), `model`, `query`, optional `top_n` (>= 1), `provider`, `session_id`, `trace`, `user`. Example model in the docs is `cohere/rerank-v3.5`; this project uses `cohere/rerank-4-fast`. Example response: `{ "id", "model", "results": [{ "document": { "text" }, "index", "relevance_score" }], "usage": { "search_units", "total_tokens" } }`. The docs also mention a `provider` field on the response (example `"Cohere"`).

### Suggested HPO timeout

Twelve trials on 20 questions, some with rerank, plus I/O, should be given a CLI timeout with headroom under the 3-hour run limit and under `terminal.timeout` (7200s). A starting point is 2400 seconds (40 minutes), which the skill can pass as `--timeout 2400`. HPO-007 records whether that was enough.

### Memory and heartbeats

The Hermes service stays at `mem_limit: 4g` in `compose.hermes.yml`. Localizing the 154 MB index and avoiding a per-trial S3 fetch is the main mitigation. If a trial still approaches the limit, reduce eval concurrency rather than raising the container memory in this PRD. The existing shim heartbeat (30s) covers one long `deepfix hpo` process.

### Test plan

- Unit and integration, local: `uv run --env-file .env pytest -v -m "not network and not e2e"`. Covers lazy LLM construction, the OpenRouter rerank client against a local HTTP server, shim env injection, HPO `--seed` / `--timeout` / file storage / local cache, the small search space invariants, and skill / prompt text. No `unittest.mock` of domain code, matching `AGENTS.md`.
- Image or Compose smoke after HPO-006: `deepfix hpo --help` shows `--seed` and `--timeout`; `GET /v1/skills` includes `hpo-search` and `optimization-loop`.
- Operator validation: HPO-007.

### Phased milestones

| Phase | Stories | Done when |
| :--- | :--- | :--- |
| 1. Blockers | HPO-001, HPO-002, HPO-003 | Retrieval-only hybrid works without `LLM_MODEL`; rerank hits `/api/v1/rerank`; shim restores env without the s6 workaround. |
| 2. HPO usability | HPO-004, HPO-005 | Seeded 20-question trials, run-dir cache, timeout, file study, small space with rerank and conditional weights. |
| 3. Agent contract | HPO-006 | Skills and prompt drive one HPO call and a full-dev gate; 40-turn cap unchanged. |
| 4. Report | HPO-007 | Bundestag 500-doc rerun is reported; nothing is promoted. |

## Success Metrics

- A retrieval-only hybrid eval with expansion 1 and `LLM_MODEL` unset scores a fixture dataset without `ModelNotConfigured`.
- A unit test of the rerank client records `POST /api/v1/rerank` and never `/v2/rerank`.
- A shim test with stripped env still runs a child that sees `LLM_MODEL` and `OPENROUTER_API_KEY`, with no secrets on argv.
- `deepfix hpo` with the small YAML, `--n-trials 12 --limit 20 --seed 0`, a file storage URI, and a local cache completes (or stops at `--timeout`) and leaves `hpo_trials.json` plus a best-so-far YAML.
- The optimization-loop skill and the Hermes prompt no longer forbid `deepfix hpo`; they still forbid promotion and `run-diagnostic`; `HERMES_MAX_TURNS` is still 40.
- On the HPO-007 rerun, Hermes writes a valid `result.json` without hitting the turn cap as the reason for a missing file, rerank is measurable, and peak memory stays under 4 GB. No live config is promoted.

## Open Questions

1. Is `--timeout 2400` the right default for the skill’s HPO command, or should HPO-007’s measured wall time set it? Starting point is 2400 seconds **(assumption)**.
2. Does OpenRouter’s live rerank response always include `document.text`, or should the client treat `index` + `relevance_score` as sufficient? The documented example includes both; the client should not require `document.text` if `index` is present **(assumption)**.
3. Should `deepfix hpo` also accept `--index-dir` as a first-class flag (mirroring `eval`), or is mutating the trial config onto `<run_dir>/idx-cache` enough? Prefer a first-class flag so the agent does not invent a copy procedure **(assumption)**.
4. How close to 4 GB do 12 local trials plus rerank actually get? HPO-007 reports this; no memory-limit change is in scope unless that run OOMs.
5. OpenRouter rerank cost and rate limits for roughly half of 12 trials × 20 queries are not measured here. HPO-007 should note errors or throttling if they appear. No change to the model id unless the owner says so.

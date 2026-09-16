"""Run DeepEval scoring over a dataset using the RAG pipeline."""

import csv
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import litellm
import mlflow
import tiktoken
from deepeval.metrics import ContextualPrecisionMetric, FaithfulnessMetric, GEval
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.mlflow_candidate import ensure_candidate_logged_model

logger = logging.getLogger(__name__)
os.environ["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] = "false"

if settings.MLFLOW_TRACKING_URI:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text or ""))


class ProxyJudgeLLM(DeepEvalBaseLLM):
    def __init__(self, model: str, api_base: str, api_key: str):
        self.model, self.api_base, self.api_key = model, api_base, api_key

    def load_model(self):
        return self.model

    def generate(self, prompt: str) -> str:
        import re
        r = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            api_base=self.api_base,
            api_key=self.api_key,
            temperature=0.0,
        )
        val = r.choices[0].message.content
        if "{" in val and "}" in val:
            match = re.search(r'\{.*\}', val, re.DOTALL)
            if match:
                return match.group(0)
        return val

    async def a_generate(self, prompt: str) -> str:
        import re
        r = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            api_base=self.api_base,
            api_key=self.api_key,
            temperature=0.0,
        )
        val = r.choices[0].message.content
        if "{" in val and "}" in val:
            match = re.search(r'\{.*\}', val, re.DOTALL)
            if match:
                return match.group(0)
        return val

    def get_model_name(self) -> str:
        return self.model


DEFLECTION_PHRASES = (
    "i do not have enough information",
    "i don't have enough information",
    "not enough information",
    "i cannot answer",
    "the context does not provide",
    "the provided text does not",
    "not mentioned in the context",
    "i am unable to answer",
    "does not contain the answer to this question",
)


def is_deflection(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(phrase in lowered for phrase in DEFLECTION_PHRASES)


def load_qa_dataset(dataset_path: Path) -> list[dict]:
    path = Path(dataset_path)
    if path.is_file():
        with open(path, "r", encoding="utf-8-sig") as f:
            return [json.loads(line) for line in f if line.strip()]

    from datasets import load_dataset
    ds = load_dataset(str(dataset_path).replace("\\\\", "/"), "qa", split="test")
    return [dict(row) for row in ds]


def run_evaluation(
    dataset_path: Path,
    config: RAGConfig,
    index_dir: str | None = None,
    limit: int | None = None,
    output_csv: str | None = "eval_results.csv",
    workspace: str = "dev",
) -> dict:
    workspace_env = "MLFLOW_TEST_WORKSPACE" if workspace == "test" else "MLFLOW_DEV_WORKSPACE"
    if hasattr(mlflow, "set_workspace") and getattr(settings, workspace_env, None):
        mlflow.set_workspace(getattr(settings, workspace_env))
    experiment_name = settings.MLFLOW_DEV_RAG_EXPERIMENT
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise RuntimeError(
            f"MLflow experiment '{experiment_name}' does not exist in this workspace. "
            "Refusing to auto-create per contract - check MLFLOW_DEV_RAG_EXPERIMENT."
        )
    mlflow.set_experiment(experiment_id=experiment.experiment_id)

    candidate_model_id = ensure_candidate_logged_model()
    if hasattr(mlflow, "set_active_model"):
        mlflow.set_active_model(model_id=candidate_model_id)

    qa_pairs = load_qa_dataset(dataset_path)
    if limit:
        qa_pairs = qa_pairs[:limit]

    # Judge configuration aligned with current Settings
    judge_api_base = settings.LLM_BASE_URL
    judge_api_key = settings.LLM_API_KEY or ""

    judge = ProxyJudgeLLM(
        model="gpt-oss-120b",
        api_base=judge_api_base,
        api_key=judge_api_key,
    )

    def make_metrics():
        return [
            FaithfulnessMetric(threshold=0.80, model=judge, async_mode=False),
            ContextualPrecisionMetric(threshold=0.75, model=judge, async_mode=False),
            GEval(
                name="Answer Correctness",
                criteria="Determine whether the actual output is factually correct and complete relative to the expected output.",
                evaluation_params=[
                    LLMTestCaseParams.ACTUAL_OUTPUT,
                    LLMTestCaseParams.EXPECTED_OUTPUT,
                ],
                threshold=0.75,
                model=judge,
                async_mode=False,
            ),
        ]

    ALL_KEYS = ["Faithfulness", "Contextual Precision", "Answer Correctness [GEval]"]

    rows = []
    deflections = 0
    errors = []

    with mlflow.start_run(run_name="src_migration_eval_run"):
        mlflow.log_param("retriever", config.retrieval.backend.value if hasattr(config.retrieval.backend, "value") else str(config.retrieval.backend))
        mlflow.log_param("embedding_model", settings.EMBEDDING_MODEL)
        mlflow.log_param("generation_model", settings.LLM_MODEL)
        mlflow.log_param("judge_model", "gpt-oss-120b")
        mlflow.log_param("n_questions", len(qa_pairs))

        import asyncio
        from deepfix_rag.pipeline import RAGWorkflow

        print(f"Instantiating LlamaIndex RAG Workflow from index: {index_dir}")
        workflow = RAGWorkflow(config=config, index_dir=index_dir)

        for i, qa in enumerate(qa_pairs):
            query = qa.get("question") or qa.get("query", "")
            expected_output = qa.get("answer") or qa.get("expected_output", "N/A")
            question_id = qa.get("id", f"q_{i}")

            try:
                print(f"Evaluating Question {i+1}/{len(qa_pairs)}: {query[:50]}...")
                response = asyncio.run(workflow.run(query=query))

                # Extract response text and source nodes
                answer = getattr(response, "response", str(response))
                source_nodes = getattr(response, "source_nodes", [])
                context = [n.node.get_content() for n in source_nodes] if source_nodes else ["No context retrieved."]

            except Exception as e:
                traceback.print_exc()
                errors.append({"index": i, "stage": "workflow_execution", "error": str(e)})
                continue

            if not answer or answer.strip() == "":
                answer = "Error: The model failed to generate any output."

            if is_deflection(answer):
                deflections += 1

            tc = LLMTestCase(
                input=query,
                actual_output=answer,
                expected_output=expected_output or "N/A",
                retrieval_context=context or ["No context provided."],
            )

            row = {"index": i, "question": query, "answer": answer}
            for key in ALL_KEYS:
                row[f"{key}_score"] = None
                row[f"{key}_pass"] = None

            for metric, key in zip(make_metrics(), ALL_KEYS):
                last_err = None
                for attempt in range(3):
                    try:
                        metric.measure(tc)
                        row[f"{key}_score"] = metric.score
                        row[f"{key}_pass"] = metric.is_successful()
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        print(f"[metric error] question={i} metric={key} attempt={attempt} -> {e}")
                        time.sleep(1.5)
                if last_err is not None:
                    errors.append({"index": i, "stage": key, "error": str(last_err)})
                time.sleep(0.2)
            rows.append(row)

        if output_csv:
            with open(output_csv, "w", newline="", encoding="utf-8") as f:
                if rows:
                    fieldnames = ["index", "question", "answer"] + [
                        f"{k}_{s}" for k in ALL_KEYS for s in ("score", "pass")
                    ]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

        def avg(key):
            vals = [r[key] for r in rows if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else None

        summary = {
            "total": len(qa_pairs),
            "scored": len(rows),
            "errors": len(errors),
            "deflection_rate": deflections / len(rows) if rows else 0.0,
            "faithfulness_avg": avg("Faithfulness_score"),
            "contextual_precision_avg": avg("Contextual Precision_score"),
            "answer_correctness_avg": avg("Answer Correctness [GEval]_score"),
        }

        if summary["faithfulness_avg"] is not None:
            mlflow.log_metric("faithfulness", summary["faithfulness_avg"])
        if summary["contextual_precision_avg"] is not None:
            mlflow.log_metric("contextual_precision", summary["contextual_precision_avg"])
        if summary["answer_correctness_avg"] is not None:
            mlflow.log_metric("answer_correctness", summary["answer_correctness_avg"])
        mlflow.log_metric("errors", summary["errors"])
        mlflow.log_metric("deflection_rate", summary["deflection_rate"])
        if output_csv:
            mlflow.log_artifact(output_csv)

    return {**summary, "rows": rows, "error_details": errors}

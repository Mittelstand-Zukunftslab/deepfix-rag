"""Run DeepEval scoring over a dataset using the RAG pipeline."""

import csv
import json
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import time
import traceback
from pathlib import Path

from deepeval.metrics import ContextualPrecisionMetric, FaithfulnessMetric, GEval
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from dotenv import load_dotenv
import litellm
import mlflow

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.evaluation.mlflow_candidate import ensure_candidate_logged_model
from deepfix_rag.retrieval.models import GenerationClient
from deepfix_rag.retrieval.pipeline import execute_retrieval

load_dotenv()

if os.getenv("MLFLOW_RAG_USERNAME"):
    os.environ["MLFLOW_TRACKING_USERNAME"] = os.environ["MLFLOW_RAG_USERNAME"]
if os.getenv("MLFLOW_RAG_PASSWORD"):
    os.environ["MLFLOW_TRACKING_PASSWORD"] = os.environ["MLFLOW_RAG_PASSWORD"]
os.environ["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] = "false"

if os.getenv("MLFLOW_TRACKING_URI"):
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text or ""))


class ProxyJudgeLLM(DeepEvalBaseLLM):
    def __init__(self, model: str, api_base: str, api_key: str):
        self.model, self.api_base, self.api_key = model, api_base, api_key

    def load_model(self):
        return self.model

    def generate(self, prompt: str, schema=None) -> str:
        r = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            api_base=self.api_base,
            api_key=self.api_key,
            custom_llm_provider="openai",
            timeout=90,
            max_tokens=4096,
        )
        return r.choices[0].message.content

    async def a_generate(self, prompt: str, schema=None) -> str:
        r = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            api_base=self.api_base,
            api_key=self.api_key,
            custom_llm_provider="openai",
            timeout=90,
            max_tokens=4096,
        )
        return r.choices[0].message.content

    def get_model_name(self):
        return self.model


DEFLECTION_PHRASES = (
    "the context does not contain the answer",
    "context does not contain",
    "provided context does not contain",
    "does not contain the answer to this question",
)


def is_deflection(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(phrase in lowered for phrase in DEFLECTION_PHRASES)


def load_qa_dataset(dataset_path: Path) -> list[dict]:
    if Path(dataset_path).is_file():
        with open(dataset_path, "r", encoding="utf-8-sig") as f:
            return [json.loads(line) for line in f]

    from datasets import load_dataset

    ds = load_dataset(str(dataset_path).replace("\\", "/"), "qa", split="test")
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
    if hasattr(mlflow, "set_workspace") and os.getenv(workspace_env):
        mlflow.set_workspace(os.environ[workspace_env])
    experiment_name = os.getenv("MLFLOW_DEV_RAG_EXPERIMENT", "dev_rag_experiment")
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise RuntimeError(
            f"MLflow experiment '{experiment_name}' does not exist in this workspace. "
            "Refusing to auto-create per contract — check MLFLOW_DEV_RAG_EXPERIMENT."
        )
    mlflow.set_experiment(experiment_id=experiment.experiment_id)

    candidate_model_id = ensure_candidate_logged_model()
    if hasattr(mlflow, "set_active_model"):
        mlflow.set_active_model(model_id=candidate_model_id)

    qa_pairs = load_qa_dataset(dataset_path)
    if limit:
        qa_pairs = qa_pairs[:limit]

    generator = GenerationClient(model=config.generation.model)
    judge = ProxyJudgeLLM(
        model="openai/openrouter/openai/gpt-4.1-mini",
        api_base=generator.api_base,
        api_key=generator.api_key,
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
        mlflow.log_param("retriever", "LanceDB")
        mlflow.log_param("embedding_model", config.embedding.model)
        mlflow.log_param("embedding_provider", config.embedding.provider)
        mlflow.log_param("generation_model", config.generation.model)
        mlflow.log_param("judge_model", "gpt-4.1-mini")
        mlflow.log_param("n_questions", len(qa_pairs))

        for i, qa in enumerate(qa_pairs):
            query = qa.get("question") or qa.get("query", "")
            expected_output = qa.get("answer") or qa.get("expected_output", "N/A")
            
            question_id = qa.get("id", f"q_{i}")
            try:
                with mlflow.start_span(name="rag_request", span_type="CHAIN") as parent_span:
                    if os.getenv("MLFLOW_CANDIDATE_ID"):
                        mlflow.update_current_trace(
                            metadata={
                                "candidate_id": os.getenv("MLFLOW_CANDIDATE_ID", ""),
                                "corpus_id": os.getenv("MLFLOW_CORPUS_ID", ""),
                                "question_id": str(question_id),
                            }
                        )
                    if parent_span:
                        parent_span.set_inputs({"question": query})

                    with mlflow.start_span(name="query_processing", span_type="PARSER") as span:
                        if span:
                            span.set_inputs({"question": query})
                            span.set_outputs({"query": query})

                    with mlflow.start_span(name="retrieval", span_type="RETRIEVER") as span:
                        results = execute_retrieval(query, config, index_dir=index_dir)
                        chunks = [r.text for r in results]
                        if span:
                            span.set_inputs({"question": query})
                            span.set_outputs({"retrieved_chunks": chunks})
                            span.set_attributes({"rag.retrieved_chunk_count": len(chunks)})

                    with mlflow.start_span(name="context_assembly", span_type="CHAIN") as span:
                        context = chunks
                        context_text = "\n\n".join(context)
                        context_tokens = count_tokens(context_text)
                        if span:
                            span.set_outputs({"assembled_context": context_text})
                            span.set_attributes({
                                "rag.context_chunk_count": len(context),
                                "rag.context_tokens": context_tokens,
                            })

                    with mlflow.start_span(name="generation", span_type="LLM") as span:
                        answer, usage = generator.generate(query, context, return_usage=True)
                        if span:
                            span.set_inputs({"question": query})
                            span.set_outputs({"answer": answer})
                            span.set_attributes({
                                **usage,
                                "rag.question_tokens": count_tokens(query),
                                "rag.context_tokens": context_tokens,
                            })

                    if parent_span:
                        parent_span.set_outputs({"answer": answer})
            except Exception as e:
                traceback.print_exc()
                errors.append({"index": i, "stage": "retrieval_or_generation", "error": str(e)})
                continue

            if is_deflection(answer):
                deflections += 1

            tc = LLMTestCase(
                input=query,
                actual_output=answer or "",
                expected_output=expected_output,
                retrieval_context=context,
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

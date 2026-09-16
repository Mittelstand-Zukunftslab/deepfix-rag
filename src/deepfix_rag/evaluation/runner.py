"""Run DeepEval scoring over a dataset using the RAG pipeline."""

import csv
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path
import time
import traceback

import asyncio

from deepeval.metrics import ContextualPrecisionMetric, FaithfulnessMetric, AnswerRelevancyMetric, ContextualRelevancyMetric, ContextualRecallMetric
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
import mlflow
from deepeval.metrics import BaseMetric
import logging
from deepfix_rag.config.schema import RAGConfig, GenerationMetricType
from deepfix_rag.config.settings import settings
from deepfix_rag.evaluation.mlflow_candidate import ensure_candidate_logged_model
from deepfix_rag.pipeline import RAGWorkflow
from deepfix_rag.evaluation.llm import ProxyJudgeLLM

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")

logger = logging.getLogger("DeepFix RAG Eval Runner")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

def count_tokens(text: str) -> int:
    return len(_enc.encode(text or ""))


DEFLECTION_PHRASES = (
    "the context does not contain the answer",
    "context does not contain",
    "provided context does not contain",
    "does not contain the answer to this question",
)

def is_deflection(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(phrase in lowered for phrase in DEFLECTION_PHRASES)

#TODO: integrate with dataloader module
def load_qa_dataset(dataset_path: Path) -> list[dict]:
    if Path(dataset_path).is_file():
        with open(dataset_path, "r", encoding="utf-8-sig") as f:
            return [json.loads(line) for line in f]

    from datasets import load_dataset

    ds = load_dataset(str(dataset_path).replace("\\", "/"), "qa", split="test")
    return [dict(row) for row in ds]

def setup_mlflow(workspace: str):
    workspace_env = "MLFLOW_TEST_WORKSPACE" if workspace == "test" else "MLFLOW_DEV_WORKSPACE"
    if hasattr(mlflow, "set_workspace") and getattr(settings, workspace_env, None):
        mlflow.set_workspace(getattr(settings, workspace_env))
    
    experiment = mlflow.get_experiment_by_name(settings.MLFLOW_DEV_RAG_EXPERIMENT)
    if experiment is None:
        raise RuntimeError(
            f"MLflow experiment '{settings.MLFLOW_DEV_RAG_EXPERIMENT}' does not exist in this workspace. "
            "Refusing to auto-create per contract - check MLFLOW_DEV_RAG_EXPERIMENT."
        )
    mlflow.set_experiment(experiment_id=experiment.experiment_id)

    candidate_model_id = ensure_candidate_logged_model()
    if hasattr(mlflow, "set_active_model"):
        mlflow.set_active_model(model_id=candidate_model_id)
    
    return None

def make_metrics(generation_metrics:list[GenerationMetricType],async_mode: bool, threshold:float, judge: ProxyJudgeLLM) -> dict[GenerationMetricType,BaseMetric]:
    kwargs = dict(async_mode=async_mode,include_reason=True,threshold=threshold, model=judge)
    metrics_map = {
        GenerationMetricType.FAITHFULNESS: FaithfulnessMetric,
        GenerationMetricType.CONTEXT_PRECISION: ContextualPrecisionMetric,
        GenerationMetricType.CONTEXT_RELEVANCY: ContextualRelevancyMetric,
        GenerationMetricType.CONTEXT_RECALL: ContextualRecallMetric,
        GenerationMetricType.ANSWER_RELEVANCY: AnswerRelevancyMetric
    }
    metrics_dict: dict[str, BaseMetric] = {}
    for metric in generation_metrics:
        if metric in metrics_map:
            metrics_dict[metric] = metrics_map[metric](**kwargs)
    return metrics_dict

async def _run_one_case(qa:dict[str,str],i:int,workflow:RAGWorkflow, metrics:dict[str,BaseMetric], num_qa_pairs: int)->dict:
    query = qa.get("question") or qa.get("query", "")
    expected_output = qa.get("answer") or qa.get("expected_output", "N/A")
    #question_id = qa.get("id", f"q_{i}")
    deflection = False
    try:
        logger.info(f"Evaluating Question {i+1}/{num_qa_pairs}: {query[:50]}...")
        response = await workflow.run(query=query)            
        answer = response.result            
        context = [n.node.get_content() for n in response.source_nodes]
        
    except Exception as e:
        return {"index": i, "error": str(e),"question": query, "answer": None, 'deflection':deflection}            

    # Fallback for LLM Empty Response so eval does not crash the loop
    if not answer or answer.strip() == "":
        answer = "Error: The model failed to generate any output."

    if is_deflection(answer):
        deflection = True

    tc = LLMTestCase(
        input=query,
        actual_output=answer,
        expected_output=expected_output or "N/A",
        retrieval_context=context or ["No context provided."],
    )

    row = {"index": i, "question": query, "answer": answer, "error": None, 'deflection':deflection}
    for metric_type in metrics.keys():
        row[f"{metric_type}_score"] = None
        row[f"{metric_type}_pass"] = None

    for metric_type, metric in metrics.items():
        score, reason, success = await metric.a_measure(tc)
        row[f"{metric_type}_score"] = score
        row[f"{metric_type}_pass"] = success
    
    return row

async def run_evaluation(
    dataset_path: Path,
    config: RAGConfig,
    index_dir: str | None = None,
    limit: int | None = None,
    output_csv: str | None = "eval_results.csv",
    workspace: str = "dev",
    run_name: str = "eval_run",
    synchronous: bool = False,
) -> dict:

    setup_mlflow(workspace)

    #TODO: integrate with dataloader module
    qa_pairs = load_qa_dataset(dataset_path)
    if limit:
        qa_pairs = qa_pairs[:limit]    
    
    judge = ProxyJudgeLLM(
        model=settings.LLM_JUDGE_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
    )

    metrics = make_metrics(config.eval.generation_metrics,async_mode=not synchronous, threshold=config.eval.threshold, judge=judge)
    
    rows = []
    errors = []

    workflow = RAGWorkflow(config=config, index_dir=index_dir)
    
    def avg(key:str)->float:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(config.model_dump(),synchronous=synchronous)
        mlflow.log_param("n_questions", len(qa_pairs),synchronous=synchronous)
        
        num_qa_pairs = len(qa_pairs)
        for i, qa in enumerate(qa_pairs):
            row = await _run_one_case(qa,i,workflow,metrics,num_qa_pairs)
            if row["error"] is not None:
                errors.append(row)
            rows.append(row)        

        summary = {
            "total": len(qa_pairs),
            "scored": len(rows),
            "errors": len(errors),
            "deflection_rate": sum(r.get('deflection',False) for r in rows) / len(rows) if rows else 0.0,
        }

        for metric_type in metrics.keys():
            summary[f"{metric_type}_avg"] = avg(f"{metric_type}_score")
        
        mlflow.log_metrics(summary,synchronous=synchronous)
        
    return {**summary, "rows": rows, "error_details": errors}

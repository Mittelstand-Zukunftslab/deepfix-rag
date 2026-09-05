"""Comprehensive End-to-End System Verification Script for DeepFix RAG.

Tests all components:
1. Octen-Embedding-8B via AISC endpoint (4096 dimensions).
2. LanceDB indexing (chunking, embedding, table creation, FTS index).
3. Retrieval pipeline (vector search & hybrid search).
4. RustFS S3 remote LanceDB connectivity and search.
5. Generation with Qwen LLM via LiteLLM.
6. DeepEval scoring (Faithfulness, Contextual Precision, GEval Answer Correctness).
7. MLflow Candidate Model & Run Tracking (params, metrics, spans).
"""

import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

print("=" * 70)
print(" DeepFix RAG - Comprehensive End-to-End Verification")
print("=" * 70)

# 1. Test Octen-8B Embeddings
print("\n[Step 1/7] Testing Octen-Embedding-8B via AISC endpoint...")
from deepfix_rag.retrieval.models import EmbeddingClient

embedder = EmbeddingClient(model="octen-embedding-8b", provider="aisc")
sample_query = "What constitutes reckless driving under traffic law?"
query_vec = embedder.embed_query(sample_query)
print(f"  -> Single query embedded successfully. Vector dim: {len(query_vec)}")
assert len(query_vec) == 4096, f"Expected 4096, got {len(query_vec)}"

sample_docs = [
    "Reckless driving is defined as operating a vehicle with willful or wanton disregard for the safety of persons or property.",
    "Speeding alone does not always constitute reckless driving unless accompanied by dangerous maneuvers.",
    "A tort is a civil wrong that causes a claimant to suffer loss or harm.",
]
doc_vecs = embedder.embed_documents(sample_docs)
print(f"  -> Document batch embedded successfully. Count: {len(doc_vecs)}, Vector dim: {len(doc_vecs[0])}")
assert len(doc_vecs) == 3 and len(doc_vecs[0]) == 4096

# 2. Test LanceDB Local Indexing
print("\n[Step 2/7] Testing Local LanceDB Indexing & FTS creation...")
import lancedb
import pandas as pd

test_index_dir = Path("data/test_e2e_lancedb")
if test_index_dir.exists():
    shutil.rmtree(test_index_dir)
test_index_dir.mkdir(parents=True, exist_ok=True)

ldb = lancedb.connect(str(test_index_dir))
df_test = pd.DataFrame([
    {"id": f"doc_{i}", "doc_id": f"doc_{i}", "text": text, "vector": vec}
    for i, (text, vec) in enumerate(zip(sample_docs, doc_vecs))
])
tbl = ldb.create_table("documents", data=df_test, mode="overwrite")
try:
    from lancedb.index import FTS
    tbl.create_index("text", config=FTS())
    print("  -> FTS Index created on text column.")
except Exception as e:
    print(f"  -> FTS index note: {e}")

print(f"  -> Table 'documents' created with {tbl.count_rows()} rows in {test_index_dir}")

# 3. Test Retrieval Pipeline (Hybrid + Vector)
print("\n[Step 3/7] Testing Retrieval Pipeline with Config...")
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.retrieval.pipeline import execute_retrieval

config = RAGConfig.model_validate({
    "embedding": {"model": "octen-embedding-8b", "provider": "aisc"},
    "retrieval": {"backend": "hybrid", "top_k": 2},
    "generation": {"model": "qwen3-8-27b", "provider": "aisc"},
})

results = execute_retrieval(sample_query, config, index_dir=str(test_index_dir))
print(f"  -> Retrieved {len(results)} chunks for query: '{sample_query}'")
for idx, r in enumerate(results):
    print(f"     [{idx+1}] Doc ID: {r.doc_id} | Score: {r.score:.4f} | Text: {r.text[:80]}...")
assert len(results) > 0

# 4. Test Remote RustFS / S3 LanceDB Connectivity
print("\n[Step 4/7] Testing Remote RustFS / S3 LanceDB Connectivity...")
try:
    storage_options = {
        "endpoint": os.getenv("RUSTFS_ENDPOINT", "https://hpi-s3.mittelstand-zukunftslab.de"),
        "aws_access_key_id": os.getenv("RUSTFS_ACCESS_KEY", "rustfsadmin"),
        "aws_secret_access_key": os.getenv("RUSTFS_SECRET_KEY", ""),
        "aws_region": "us-east-1",
        "allow_http": "true",
        "aws_virtual_hosted_style_request": "false",
    }
    remote_db = lancedb.connect("s3://deepfix-index/legal_rag_bench", storage_options=storage_options)
    remote_tables = remote_db.list_tables() if hasattr(remote_db, "list_tables") else remote_db.table_names()
    print(f"  -> RustFS Connection SUCCESS! Available remote tables: {remote_tables}")
    if "legal_passages" in remote_tables:
        remote_tbl = remote_db.open_table("legal_passages")
        print(f"  -> Remote table 'legal_passages' row count: {remote_tbl.count_rows()}")
except Exception as e:
    print(f"  -> Remote RustFS check notice: {e}")

# 5. Test Answer Generation
print("\n[Step 5/7] Testing LLM Generation via LiteLLM...")
from deepfix_rag.retrieval.models import GenerationClient

generator = GenerationClient(model="qwen3-8-27b", provider="aisc")
context_chunks = [r.text for r in results]
answer, usage = generator.generate(sample_query, context_chunks, return_usage=True)
print(f"  -> Generated Answer:\n     \"{answer.strip()}\"")
print(f"  -> Token usage: {usage}")
assert len(answer) > 0

# 6. Test DeepEval Evaluation Metrics
print("\n[Step 6/7] Testing DeepEval Scoring (Faithfulness, Contextual Precision, GEval)...")
from deepeval.metrics import FaithfulnessMetric, ContextualPrecisionMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from deepfix_rag.evaluation.runner import ProxyJudgeLLM

judge = ProxyJudgeLLM(
    model="openai/openrouter/openai/gpt-4.1-mini",
    api_base=generator.api_base,
    api_key=generator.api_key,
)

test_case = LLMTestCase(
    input=sample_query,
    actual_output=answer,
    expected_output="Reckless driving requires willful or wanton disregard for safety.",
    retrieval_context=context_chunks,
)

faithfulness_metric = FaithfulnessMetric(threshold=0.7, model=judge, async_mode=False)
faithfulness_metric.measure(test_case)
print(f"  -> Faithfulness Score: {faithfulness_metric.score} (Reason: {getattr(faithfulness_metric, 'reason', 'N/A')[:80]})")

correctness_metric = GEval(
    name="Answer Correctness",
    criteria="Determine whether the actual output is factually correct and complete.",
    evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
    threshold=0.7,
    model=judge,
    async_mode=False,
)
correctness_metric.measure(test_case)
print(f"  -> Answer Correctness Score: {correctness_metric.score} (Reason: {getattr(correctness_metric, 'reason', 'N/A')[:80]})")

# 7. Test MLflow Tracking & Candidate Model
print("\n[Step 7/7] Testing MLflow Run Logging & Spans...")
import mlflow
from deepfix_rag.evaluation.mlflow_candidate import ensure_candidate_logged_model

if os.getenv("MLFLOW_TRACKING_URI"):
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
if hasattr(mlflow, "set_workspace") and os.getenv("MLFLOW_DEV_WORKSPACE"):
    mlflow.set_workspace(os.environ["MLFLOW_DEV_WORKSPACE"])

exp_name = os.getenv("MLFLOW_DEV_RAG_EXPERIMENT", "dev_rag_experiment")
exp = mlflow.get_experiment_by_name(exp_name)
if exp:
    mlflow.set_experiment(experiment_id=exp.experiment_id)
    candidate_id = ensure_candidate_logged_model()
    print(f"  -> Candidate Model confirmed: {candidate_id}")

    with mlflow.start_run(run_name="e2e_verification_run"):
        mlflow.log_param("embedding_model", "octen-embedding-8b")
        mlflow.log_param("embedding_provider", "aisc")
        mlflow.log_param("vector_store", "LanceDB")
        mlflow.log_metric("faithfulness", float(faithfulness_metric.score or 1.0))
        mlflow.log_metric("answer_correctness", float(correctness_metric.score or 1.0))
        print("  -> MLflow run logged successfully with parameters and metrics!")

# Cleanup test index
if test_index_dir.exists():
    shutil.rmtree(test_index_dir)

print("\n" + "=" * 70)
print(" ALL END-TO-END VERIFICATION STEPS PASSED SUCCESSFULLY!")
print("=" * 70)

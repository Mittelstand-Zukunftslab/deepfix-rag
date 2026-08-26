import os
import mlflow
from dotenv import load_dotenv

load_dotenv()

required = [
    "MLFLOW_TRACKING_URI",
    "MLFLOW_RAG_USERNAME",
    "MLFLOW_RAG_PASSWORD",
    "MLFLOW_DEV_WORKSPACE",
]

for name in required:
    if not os.getenv(name):
        raise RuntimeError(f"Missing required environment variable: {name}")

# Map RAG credentials to tracking credentials
os.environ["MLFLOW_TRACKING_USERNAME"] = os.environ["MLFLOW_RAG_USERNAME"]
os.environ["MLFLOW_TRACKING_PASSWORD"] = os.environ["MLFLOW_RAG_PASSWORD"]

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_workspace(os.environ["MLFLOW_DEV_WORKSPACE"])

system_prompt = (
    "Answer the question using only the context provided. "
    "If the context does not contain the answer, say so."
)

print("Attempting to register prompt with MLflow server...")

# We use the new .genai namespace to avoid the warning
prompt = mlflow.genai.register_prompt(
    name="target-rag-system",
    template=system_prompt,
)

print("\nSUCCESS! Your prompt URI is:")
print(prompt.uri)
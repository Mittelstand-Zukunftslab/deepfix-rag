import os
import mlflow
from deepfix_rag.config.settings import settings

required = [
    "MLFLOW_TRACKING_URI",
    "MLFLOW_TRACKING_USERNAME",
    "MLFLOW_TRACKING_PASSWORD",
    "MLFLOW_DEV_WORKSPACE",
]

for name in required:
    if not getattr(settings, name, None):
        raise RuntimeError(f"Missing required configuration setting matching environment variable: {name}")

# Map RAG credentials to tracking credentials
os.environ["MLFLOW_TRACKING_USERNAME"] = settings.MLFLOW_TRACKING_USERNAME
os.environ["MLFLOW_TRACKING_PASSWORD"] = settings.MLFLOW_TRACKING_PASSWORD

mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
mlflow.set_workspace(settings.MLFLOW_DEV_WORKSPACE)

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

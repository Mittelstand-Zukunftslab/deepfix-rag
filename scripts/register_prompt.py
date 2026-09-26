import mlflow

from deepfix_rag.config.settings import settings

if settings.MLFLOW_TRACKING_URI:
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

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

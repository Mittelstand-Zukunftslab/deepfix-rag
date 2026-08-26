import os
import mlflow
from dotenv import load_dotenv

load_dotenv()

os.environ["MLFLOW_TRACKING_USERNAME"] = os.environ["MLFLOW_RAG_USERNAME"]
os.environ["MLFLOW_TRACKING_PASSWORD"] = os.environ["MLFLOW_RAG_PASSWORD"]

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_workspace(os.environ["MLFLOW_DEV_WORKSPACE"])

for name in ["target-rag-system-main", "target-rag-system-rewrite"]:
    try:
        prompt = mlflow.genai.load_prompt(f"prompts:/{name}@latest")
        print(f"{name} -> {prompt.uri}")
        print("  template:", prompt.template[:200])
    except Exception as e:
        print(f"{name} -> ERROR: {e}")
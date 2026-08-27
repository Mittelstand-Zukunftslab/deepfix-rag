from pathlib import Path

from deepfix_mlflow.hermes_tracing import HermesMLflowTracingConfig
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]

load_dotenv(ROOT / ".env")

config = HermesMLflowTracingConfig.from_env()

print(config.redacted_summary())
print(config.hermes_otel_backend_yaml())
print(config.exporter_environment()["MLFLOW_HERMES_OTLP_AUTHORIZATION"])
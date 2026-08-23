---
name: add-hermes-mlflow-tracing
description: Install or repair Hermes-to-MLflow tracing using the supported hermes-otel plugin and the dedicated hermes-service identity. Use for Hermes telemetry setup only. Do not configure Hermes Target-RAG read access, change MLflow RBAC, or build custom observer hooks unless the plugin cannot satisfy the contract.
---

# Add Hermes MLflow Tracing

Use `hermes-otel`; do not build custom Hermes observer tracing while the supported plugin satisfies the contract.

## Inputs

Use only `MLFLOW_TRACKING_URI`, `MLFLOW_DEV_WORKSPACE`, `MLFLOW_DEV_HERMES_EXPERIMENT`, `MLFLOW_HERMES_SERVICE_USERNAME`, and `MLFLOW_HERMES_SERVICE_PASSWORD`.

The tracing path must not use the `hermes-reader` identity.

Server target: `hermes-service` may write Hermes traces to the `hermes` experiment in `dev-workspace`; it has no Target RAG read grant and no `test-workspace` access.

## Workflow

1. Inspect the actual Hermes installation and plugin configuration.
2. Install the supported plugin:

```bash
hermes plugins install briancaffey/hermes-otel
```

3. Ensure the Hermes runtime has the plugin's OpenTelemetry dependencies according to the plugin's current instructions.
4. Resolve the existing `hermes` experiment ID. Do not create a new experiment.
5. Configure `~/.hermes/plugins/hermes_otel/config.yaml` with the MLflow OTLP backend at `<MLFLOW_TRACKING_URI>/v1/traces`.
6. Set `x-mlflow-experiment-id` to the existing Hermes experiment ID and `X-MLFLOW-WORKSPACE` to `dev-workspace`.
7. Supply Basic Auth through `${MLFLOW_HERMES_OTLP_AUTHORIZATION}`; never commit the literal token. `deepfix_mlflow.hermes_tracing.HermesMLflowTracingConfig` can generate the header and config fragment.
8. Keep `metrics: false` and `logs: false` unless the project explicitly expands the MLflow contract.
9. Run one Hermes turn that invokes at least one tool and inspect the emitted trace.

## Required evidence

Verify AGENT/root turn span, LLM spans, TOOL spans, token usage, latency, session metadata, and `candidate_id` when the execution is associated with a Target RAG candidate.

If the plugin does not expose `candidate_id` automatically, add the smallest supported metadata propagation at the Hermes/plugin boundary. Do not replace the plugin with custom tracing for this alone.

## Security verification

Verify traces reach only the `hermes` experiment, `hermes-service` cannot read Target RAG evidence, `hermes-service` cannot access `test-workspace`, plugin config contains no literal password/token, and logs do not print Authorization.

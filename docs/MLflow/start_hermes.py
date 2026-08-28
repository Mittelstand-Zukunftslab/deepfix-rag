from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from deepfix_mlflow.hermes_tracing import (
    HermesMLflowTracingConfig,
    HermesMLflowTracingError,
)


OTEL_CONFIG_PATH = Path(
    os.environ.get("HERMES_OTEL_CONFIG", "/tmp/hermes_otel.yaml")
)

_TRUE = {"1", "true", "yes", "on"}


def _bootstrap_mode() -> bool:
    return os.environ.get("HERMES_BOOTSTRAP_MODE", "true").strip().lower() in _TRUE


def _write_otel_config(config: HermesMLflowTracingConfig) -> None:
    data = yaml.safe_load(config.hermes_otel_backend_yaml())

    # hermes-otel's generic OTLP backend does not interpolate ${ENV_VAR}
    # placeholders in arbitrary headers, so put the already-derived Basic
    # Authorization value into this runtime-only file.
    backend = data["backends"][0]
    backend["headers"]["Authorization"] = config.authorization_header

    OTEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = OTEL_CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    tmp_path.chmod(0o600)
    tmp_path.replace(OTEL_CONFIG_PATH)


def _enable_hermes_otel() -> None:
    subprocess.run(
        ["hermes", "plugins", "enable", "hermes_otel"],
        check=True,
    )


def _scrub_service_password_inputs() -> None:
    os.environ.pop("MLFLOW_HERMES_SERVICE_USERNAME", None)
    os.environ.pop("MLFLOW_HERMES_SERVICE_PASSWORD", None)


def main() -> int:
    try:
        tracing = HermesMLflowTracingConfig.from_env()
    except (HermesMLflowTracingError, RuntimeError) as exc:
        print(
            f"[deepfix-mlflow] tracing bootstrap failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"[deepfix-mlflow] MLflow tracing route: "
        f"{tracing.redacted_summary()}"
    )

    _write_otel_config(tracing)
    _scrub_service_password_inputs()

    try:
        _enable_hermes_otel()
    except subprocess.CalledProcessError as exc:
        print(
            "[deepfix-mlflow] failed to enable hermes_otel "
            f"(exit code {exc.returncode})",
            file=sys.stderr,
        )
        return exc.returncode or 1

    print(
        f"[deepfix-mlflow] hermes-otel runtime config written to "
        f"{OTEL_CONFIG_PATH}; plugin enabled"
    )

    if _bootstrap_mode():
        print(
            "[deepfix-mlflow] HERMES_BOOTSTRAP_MODE=true: "
            "container ready for `hermes setup`."
        )
        os.execvp("sleep", ["sleep", "infinity"])

    print("[deepfix-mlflow] starting Hermes gateway")

    os.execvpe(
        "hermes",
        ["hermes", "gateway", "run"],
        os.environ,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
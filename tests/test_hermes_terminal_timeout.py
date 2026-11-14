"""``terminal.timeout`` must outlast one retrieval-only eval of the fixture corpus.

The sample uses ``--no-mlflow`` so the clock is the retrieval itself. The
configured value is the image's ``terminal.timeout`` (7200 seconds).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import time

import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_SUCCESS
from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.main import app

pytestmark = [pytest.mark.integration]

_SPEC = importlib.util.spec_from_file_location(
    "hermes_sandbox_boot_timeout",
    Path(__file__).resolve().parents[1] / "docker" / "hermes" / "sandbox_boot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_boot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_boot)


def test_terminal_timeout_exceeds_a_fixture_retrieval_eval(
    cli_runner: CliRunner,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    populated_sample_index_dir: Path,
    tmp_path: Path,
) -> None:
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(populated_sample_index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.retrieval.top_k = 3
    cfg.rerank.enabled = False
    cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
    config_path = tmp_path / "champion.yaml"
    config_path.write_text(cfg.to_yaml(), encoding="utf-8")

    started = time.monotonic()
    result = cli_runner.invoke(
        app,
        [
            "eval",
            "--retrieval-only",
            "--json",
            "--no-mlflow",
            "--config",
            str(config_path),
            "--dataset",
            str(sample_qa_path),
        ],
    )
    elapsed = time.monotonic() - started
    configured = _boot.TERMINAL_TIMEOUT_SECONDS

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert configured == 7200
    assert configured > elapsed, (
        f"terminal.timeout={configured}s sample_eval_retrieval_only={elapsed:.3f}s"
    )

"""Tests for CLI Project Scaffolding & Unified JSON Interface."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import mlflow
import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_RUNTIME_ERROR, EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.main import app
from deepfix_rag.pipeline import RAGWorkflow

pytestmark = [pytest.mark.cli, pytest.mark.unit]


def test_cli_executable_identity_and_help(cli_runner: CliRunner):
    """Verify CLI help and basic command structure."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == EXIT_SUCCESS
    assert "Deterministic CLI for RAG optimization" in result.stdout


def test_cli_subcommands_registered(cli_runner: CliRunner):
    """Verify all required subcommands exist in the top-level app."""
    subcommands = [
        "dataset",
        "eval",
        "index",
        "run-diagnostic",
        "mutate-config",
        "diff-config",
        "export",
    ]
    for cmd in subcommands:
        result = cli_runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == EXIT_SUCCESS, f"Failed for subcommand: {cmd}"


def test_cli_dataset_subcommands_help(cli_runner: CliRunner):
    """Verify dataset subcommands help pages are functional."""
    for subcmd in ["load", "list", "generate-unanswerable"]:
        result = cli_runner.invoke(app, ["dataset", subcmd, "--help"])
        assert result.exit_code == EXIT_SUCCESS, f"Failed for dataset subcommand: {subcmd}"


def test_mutate_config_json_output(
    cli_runner: CliRunner, default_config_path: Path, tmp_path: Path
):
    """Verify mutate-config emits valid JSON to stdout on success with exit code 0."""
    out_config = tmp_path / "mutated.yaml"

    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(default_config_path),
            "--set",
            "retrieval.top_k=20",
            "--output",
            str(out_config),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["applied_overrides"]["retrieval.top_k"] == 20
    assert out_config.exists()


def test_mutate_config_multiple_overrides_execution(
    cli_runner: CliRunner, default_config_path: Path, tmp_path: Path
):
    """Verify executing mutate-config with multiple --set overrides in standard output mode."""
    out_config = tmp_path / "mutated.yaml"

    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(default_config_path),
            "--set",
            "retrieval.top_k=25",
            "--set",
            "retrieval.language=english",
            "--output",
            str(out_config),
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert out_config.exists()

    with open(out_config, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["retrieval"]["top_k"] == 25
    assert data["retrieval"]["language"] == "english"


def test_mutate_config_validation_error_exit_code_and_json(cli_runner: CliRunner):
    """Verify validation error yields exit code 1 and structured JSON error when --json is passed."""
    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            "configs/non_existent_file.yaml",
            "--output",
            "configs/unused.yaml",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR
    assert "not found" in payload["error"]


def test_mutate_config_invalid_override_format_exit_code(
    cli_runner: CliRunner, default_config_path: Path
):
    """Verify malformed override yields exit code 1."""
    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(default_config_path),
            "--set",
            "invalid_override_without_equal_sign",
            "--output",
            "configs/unused.yaml",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR


def test_diff_config_json_output(cli_runner: CliRunner, default_config_path: Path, tmp_path: Path):
    """Verify diff-config outputs structured JSON to stdout."""
    mutated_config = tmp_path / "mutated.yaml"

    with open(default_config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["retrieval"]["top_k"] = 30
    with open(mutated_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)

    result = cli_runner.invoke(
        app,
        [
            "diff-config",
            "--base",
            str(default_config_path),
            "--compare",
            str(mutated_config),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "retrieval.top_k" in payload["diffs"]


def test_diff_config_human_readable_execution(
    cli_runner: CliRunner, default_config_path: Path, tmp_path: Path
):
    """Verify executing real diff-config between two YAML files with rich terminal output."""
    mutated_config = tmp_path / "mutated.yaml"

    with open(default_config_path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    content["retrieval"]["top_k"] = 40
    with open(mutated_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(content, f)

    result = cli_runner.invoke(
        app,
        [
            "diff-config",
            "--base",
            str(default_config_path),
            "--compare",
            str(mutated_config),
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert "retrieval.top_k" in result.output


def test_diff_config_missing_args_exit_code(cli_runner: CliRunner):
    """Verify missing configs produce exit code 1."""
    result = cli_runner.invoke(app, ["diff-config", "--json"])
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR


def test_dataset_list_json_output(cli_runner: CliRunner, tmp_path: Path):
    """Verify dataset list outputs clean JSON on stdout."""
    result = cli_runner.invoke(app, ["dataset", "list", "--data-root", str(tmp_path), "--json"])
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert isinstance(payload["datasets"], list)


def test_dataset_load_validation_error_split(cli_runner: CliRunner):
    """Verify dataset load rejects invalid split with exit code 1 and JSON error."""
    result = cli_runner.invoke(
        app,
        [
            "dataset",
            "load",
            "--name",
            "bundestag-qa",
            "--split",
            "invalid_split",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR


def test_eval_missing_config_fails(cli_runner: CliRunner):
    """Verify deepfix eval requires --config."""
    result = cli_runner.invoke(app, ["eval", "--json"])
    assert result.exit_code != EXIT_SUCCESS


def test_eval_help_contains_retrieval_only(cli_runner: CliRunner):
    """Verify deepfix eval help documents the --retrieval-only option."""
    result = cli_runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == EXIT_SUCCESS
    assert "--retrieval-only" in result.stdout


def test_index_missing_config_fails(cli_runner: CliRunner):
    """Verify deepfix index requires --config."""
    result = cli_runner.invoke(app, ["index", "--json"])
    assert result.exit_code != EXIT_SUCCESS


def test_export_stub_exit_code_and_json(cli_runner: CliRunner, default_config_path: Path):
    """Verify export returns exit code 2 and structured error JSON."""
    result = cli_runner.invoke(
        app,
        [
            "export",
            "--config",
            str(default_config_path),
            "--output",
            "exports/out",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_RUNTIME_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_RUNTIME_ERROR
    assert "not implemented" in payload["error"].lower()


def test_generate_unanswerable_stub_exit_code_and_json(cli_runner: CliRunner, tmp_path: Path):
    """Verify generate-unanswerable returns exit code 2 and structured error JSON."""
    result = cli_runner.invoke(
        app,
        [
            "dataset",
            "generate-unanswerable",
            "--dataset",
            str(tmp_path),
            "--output",
            str(tmp_path / "synthetic.jsonl"),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_RUNTIME_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_RUNTIME_ERROR


# =========================================================================
# deepfix eval: --index-dir scope, JSON stdout purity, all-cases-errored exit
# =========================================================================
EXPERIMENT_NAME = "dev_rag_experiment"
EVAL_RUN_COMMAND = ["eval", "--retrieval-only", "--json"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def mlflow_tracking_uri(tmp_path_factory: pytest.TempPathFactory):
    """Serve a real MLflow tracking server holding the experiment eval requires.

    Only REST-backed stores print the "View run ..." lines to stdout, so a file or sqlite
    store would not exercise the stdout contract of ``eval --json``.
    """
    root = tmp_path_factory.mktemp("mlflow_server")
    port = _free_port()
    uri = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mlflow",
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--backend-store-uri",
            f"sqlite:///{root / 'mlflow.db'}",
            "--artifacts-destination",
            str(root / "artifacts"),
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )
    try:
        deadline = time.monotonic() + 90
        while True:
            assert server.poll() is None, "mlflow server exited during startup"
            try:
                with urllib.request.urlopen(f"{uri}/health", timeout=2):
                    break
            except OSError:
                assert time.monotonic() < deadline, "mlflow server did not become healthy"
                time.sleep(0.5)
        previous_uri = mlflow.get_tracking_uri()
        mlflow.set_tracking_uri(uri)
        mlflow.create_experiment(EXPERIMENT_NAME)
        yield uri
        mlflow.set_tracking_uri(previous_uri)
    finally:
        server.terminate()
        server.wait(timeout=30)


def _build_index(index_dir: Path, corpus_path: Path) -> Path:
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.retrieval.load_if_exists = False
    cfg.data = DataLoaderConfig(path_or_repo=str(corpus_path))

    async def _ingest() -> None:
        await RAGWorkflow(config=cfg).run(ingest=True)

    asyncio.run(_ingest())
    return index_dir


@pytest.fixture
def small_index_dir(sample_corpus_path: Path, tmp_path: Path) -> Path:
    """LanceDB index holding only the first sample document (doc_de_001)."""
    first_line = sample_corpus_path.read_text(encoding="utf-8").splitlines()[0]
    one_doc_corpus = tmp_path / "one_doc_corpus.jsonl"
    one_doc_corpus.write_text(first_line + "\n", encoding="utf-8")
    return _build_index(tmp_path / "small_lancedb_index", one_doc_corpus)


def _write_eval_config(path: Path, corpus_path: Path, index_dir: Path | str) -> Path:
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.retrieval.load_if_exists = True
    cfg.retrieval.top_k = 3
    cfg.rerank.enabled = False
    cfg.data = DataLoaderConfig(path_or_repo=str(corpus_path))
    path.write_text(cfg.to_yaml(), encoding="utf-8")
    return path


@pytest.mark.integration
def test_eval_json_stdout_is_pure_json_with_mlflow_run_id(
    cli_runner: CliRunner,
    mlflow_tracking_uri: str,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    populated_sample_index_dir: Path,
    tmp_path: Path,
):
    """MLflow console lines go to stderr, so stdout parses as JSON and names the MLflow run."""
    config_path = _write_eval_config(
        tmp_path / "champion.yaml", sample_corpus_path, populated_sample_index_dir
    )

    result = cli_runner.invoke(
        app,
        [*EVAL_RUN_COMMAND, "--config", str(config_path), "--dataset", str(sample_qa_path)],
    )

    assert result.exit_code == EXIT_SUCCESS, result.stderr
    payload = json.loads(result.stdout)
    assert payload["errors"] == 0
    run_id = payload["mlflow_run_id"]
    assert isinstance(run_id, str) and run_id
    assert mlflow.get_run(run_id).info.run_id == run_id
    assert "View run" in result.stderr
    assert "View experiment" in result.stderr
    assert "View run" not in result.stdout
    assert "mlflow_run_id" not in mlflow.get_run(run_id).data.metrics


@pytest.mark.integration
def test_eval_index_dir_applies_to_candidate_only_in_compare(
    cli_runner: CliRunner,
    mlflow_tracking_uri: str,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    populated_sample_index_dir: Path,
    small_index_dir: Path,
    tmp_path: Path,
):
    """The --compare baseline is scored on its own config's index, not on --index-dir."""
    champion_path = _write_eval_config(
        tmp_path / "champion.yaml", sample_corpus_path, populated_sample_index_dir
    )
    candidate_path = _write_eval_config(
        tmp_path / "candidate.yaml", sample_corpus_path, tmp_path / "does_not_exist"
    )
    dataset_args = ["--dataset", str(sample_qa_path)]

    champion_alone = cli_runner.invoke(
        app, [*EVAL_RUN_COMMAND, "--config", str(champion_path), *dataset_args]
    )
    assert champion_alone.exit_code == EXIT_SUCCESS, champion_alone.stderr
    champion_scores = json.loads(champion_alone.stdout)

    compared = cli_runner.invoke(
        app,
        [
            *EVAL_RUN_COMMAND,
            "--config",
            str(candidate_path),
            "--index-dir",
            str(small_index_dir),
            "--compare",
            str(champion_path),
            *dataset_args,
        ],
    )
    assert compared.exit_code == EXIT_SUCCESS, compared.stderr
    payload = json.loads(compared.stdout)
    candidate, baseline = payload["candidate"], payload["baseline"]

    assert champion_scores["recall"] == pytest.approx(1.0)
    assert baseline["recall"] == pytest.approx(champion_scores["recall"])
    assert baseline["ndcg"] == pytest.approx(champion_scores["ndcg"])
    assert candidate["recall"] < baseline["recall"]
    assert payload["delta"]["recall"] == pytest.approx(candidate["recall"] - baseline["recall"])
    assert payload["delta"]["recall"] != 0
    assert candidate["mlflow_run_id"] and baseline["mlflow_run_id"]
    assert candidate["mlflow_run_id"] != baseline["mlflow_run_id"]
    assert "mlflow_run_id" not in payload["delta"]


def test_eval_help_scopes_index_dir_to_candidate(cli_runner: CliRunner):
    """The --index-dir help states that the baseline keeps its own index."""
    result = cli_runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == EXIT_SUCCESS
    flattened = " ".join(re.sub(r"[│╭╮╰╯─]", " ", result.stdout).split())
    assert "candidate only" in flattened
    assert "baseline is always scored on the index in its own config" in flattened


class _RejectingEmbeddingHandler(BaseHTTPRequestHandler):
    """Embedding endpoint answering every request with a non-retryable 400."""

    def do_POST(self) -> None:
        body = json.dumps({"error": {"message": "embedding backend rejected the request"}})
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def rejecting_embedding_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RejectingEmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


@pytest.mark.integration
def test_eval_exits_2_naming_the_error_when_every_case_errors(
    rejecting_embedding_url: str,
    mlflow_tracking_uri: str,
    sample_qa_path: Path,
    sample_corpus_path: Path,
    populated_sample_index_dir: Path,
    tmp_path: Path,
):
    """Every query embedding fails, so eval must exit 2 instead of reporting retrieval misses."""
    config_path = _write_eval_config(
        tmp_path / "champion.yaml", sample_corpus_path, populated_sample_index_dir
    )
    env = {
        **os.environ,
        "EMBEDDING_BASE_URL": rejecting_embedding_url,
        "EMBEDDING_API_KEY": "rejecting-endpoint-test-key",
        "MLFLOW_TRACKING_URI": mlflow_tracking_uri,
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "deepfix_rag.main",
            *EVAL_RUN_COMMAND,
            "--config",
            str(config_path),
            "--dataset",
            str(sample_qa_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert completed.returncode == EXIT_RUNTIME_ERROR, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_RUNTIME_ERROR
    assert "all 3 cases errored" in payload["error"]
    assert "First error:" in payload["error"]
    assert "First error: None" not in payload["error"]
    assert "rejected the request" in payload["error"]

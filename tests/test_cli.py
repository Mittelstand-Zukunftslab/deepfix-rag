"""Tests for US-001: CLI Project Scaffolding & Unified JSON Interface."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.main import app
from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR, EXIT_RUNTIME_ERROR

runner = CliRunner()


def test_cli_executable_identity_and_help():
    """Verify CLI help and basic command structure."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == EXIT_SUCCESS
    assert "Deterministic CLI for RAG optimization" in result.stdout


def test_cli_subcommands_registered():
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
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == EXIT_SUCCESS, f"Failed for subcommand: {cmd}"


def test_mutate_config_json_output(tmp_path: Path):
    """Verify mutate-config emits valid JSON to stdout on success with exit code 0."""
    base_config = Path("configs/default.yaml")
    out_config = tmp_path / "mutated.yaml"

    result = runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(base_config),
            "--set",
            "retrieval.top_k=20",
            "--output",
            str(out_config),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    # stdout must be parseable JSON
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["applied_overrides"]["retrieval.top_k"] == 20
    assert out_config.exists()


def test_mutate_config_validation_error_exit_code_and_json():
    """Verify validation error yields exit code 1 and structured JSON error when --json is passed."""
    result = runner.invoke(
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


def test_mutate_config_invalid_override_format_exit_code():
    """Verify malformed override yields exit code 1."""
    result = runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            "configs/default.yaml",
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


def test_diff_config_json_output(tmp_path: Path):
    """Verify diff-config outputs structured JSON to stdout."""
    base_config = Path("configs/default.yaml")
    mutated_config = tmp_path / "mutated.yaml"

    with open(base_config, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["retrieval"]["top_k"] = 30
    with open(mutated_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)

    result = runner.invoke(
        app,
        [
            "diff-config",
            "--base",
            str(base_config),
            "--compare",
            str(mutated_config),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "retrieval.top_k" in payload["diffs"]


def test_diff_config_missing_args_exit_code():
    """Verify missing configs produce exit code 1."""
    result = runner.invoke(app, ["diff-config", "--json"])
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_VALIDATION_ERROR


def test_dataset_list_json_output(tmp_path: Path):
    """Verify dataset list outputs clean JSON on stdout."""
    result = runner.invoke(app, ["dataset", "list", "--data-root", str(tmp_path), "--json"])
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert isinstance(payload["datasets"], list)


def test_dataset_load_validation_error_split():
    """Verify dataset load rejects invalid split with exit code 1 and JSON error."""
    result = runner.invoke(
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


def test_diagnostic_stub_exit_code_and_json():
    """Verify run-diagnostic returns exit code 2 and structured error JSON."""
    result = runner.invoke(
        app,
        [
            "run-diagnostic",
            "--dataset",
            "bundestag-qa",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_RUNTIME_ERROR
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["code"] == EXIT_RUNTIME_ERROR
    assert "not implemented" in payload["error"].lower()


def test_export_stub_exit_code_and_json():
    """Verify export returns exit code 2 and structured error JSON."""
    result = runner.invoke(
        app,
        [
            "export",
            "--config",
            "configs/default.yaml",
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


def test_generate_unanswerable_stub_exit_code_and_json(tmp_path: Path):
    """Verify generate-unanswerable returns exit code 2 and structured error JSON."""
    result = runner.invoke(
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

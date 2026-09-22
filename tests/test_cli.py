"""Tests for CLI Project Scaffolding & Unified JSON Interface."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
import yaml

from deepfix_rag.cli.ui import EXIT_RUNTIME_ERROR, EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.main import app

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

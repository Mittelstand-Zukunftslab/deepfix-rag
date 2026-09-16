from pathlib import Path
import yaml
from typer.testing import CliRunner

from deepfix_rag.main import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Deterministic CLI for RAG optimization" in result.output


def test_cli_subcommands_help():
    for subcmd in ["eval", "index", "run-diagnostic", "mutate-config", "diff-config", "export", "dataset"]:
        result = runner.invoke(app, [subcmd, "--help"])
        assert result.exit_code == 0


def test_cli_dataset_subcommands_help():
    for subcmd in ["load", "list", "generate-unanswerable"]:
        result = runner.invoke(app, ["dataset", subcmd, "--help"])
        assert result.exit_code == 0


def test_cli_mutate_config_execution(tmp_path: Path):
    """Verify executing real mutate-config with actual YAML files."""
    base_config = Path("configs/default.yaml")
    out_config = tmp_path / "mutated.yaml"

    result = runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(base_config),
            "--set",
            "retrieval.top_k=25",
            "--set",
            "retrieval.language=english",
            "--output",
            str(out_config),
        ],
    )
    assert result.exit_code == 0
    assert out_config.exists()

    with open(out_config, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["retrieval"]["top_k"] == 25
    assert data["retrieval"]["language"] == "english"


def test_cli_diff_config_execution(tmp_path: Path):
    """Verify executing real diff-config between two actual YAML files."""
    base_config = Path("configs/default.yaml")
    mutated_config = tmp_path / "mutated.yaml"

    # Create a mutated config file
    with open(base_config, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    content["retrieval"]["top_k"] = 40
    with open(mutated_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(content, f)

    result = runner.invoke(
        app,
        [
            "diff-config",
            "--base",
            str(base_config),
            "--compare",
            str(mutated_config),
        ],
    )
    assert result.exit_code == 0
    assert "retrieval.top_k" in result.output

from typer.testing import CliRunner
from deepfix_rag.main import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Deterministic CLI for RAG optimization" in result.output


def test_cli_subcommands_help():
    for subcmd in ["eval", "index", "run-diagnostic", "mutate-config", "diff-config", "export"]:
        result = runner.invoke(app, [subcmd, "--help"])
        assert result.exit_code == 0

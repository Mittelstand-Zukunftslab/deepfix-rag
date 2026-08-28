"""Tests for the dataset CLI commands and dev/test split isolation rules."""

import shutil
from pathlib import Path
from typer.testing import CliRunner

from deepfix_rag.main import app
from deepfix_rag.cli.dataset import DATA_ROOT

runner = CliRunner()


def test_dataset_load_dev_split():
    """Verify loading the dev split successfully scaffolds the standard directories."""
    result = runner.invoke(app, ["dataset", "load", "--name", "garage", "--split", "dev"])
    assert result.exit_code == 0
    assert "Loading dataset 'garage'" in result.stdout
    assert "isolated successfully for dev" in result.stdout

    expected_dir = DATA_ROOT / "garage" / "dev"
    assert expected_dir.exists()
    assert (expected_dir / "corpus.jsonl").exists()
    assert (expected_dir / "qa.jsonl").exists()


def test_dataset_load_test_split():
    """Verify loading the test split scaffolds properly."""
    result = runner.invoke(app, ["dataset", "load", "--name", "garage", "--split", "test"])
    assert result.exit_code == 0

    expected_dir = DATA_ROOT / "garage" / "test"
    assert expected_dir.exists()


def test_dataset_invalid_split():
    """Verify that any split other than dev/test is explicitly rejected to prevent leakage."""
    result = runner.invoke(app, ["dataset", "load", "--name", "garage", "--split", "train"])
    assert result.exit_code == 1
    assert "split must be 'dev' or 'test'" in result.stdout


def test_dataset_list():
    """Verify the list command displays standard layout."""
    runner.invoke(app, ["dataset", "load", "--name", "blleqa", "--split", "dev"])
    result = runner.invoke(app, ["dataset", "list"])
    
    assert result.exit_code == 0
    assert "garage" in result.stdout
    assert "blleqa" in result.stdout
    assert "dev" in result.stdout


def test_dataset_generate_unanswerable():
    """Verify synthetic unanswerable generation respects ratios and paths."""
    out_file = Path("data/synth_out.jsonl")
    result = runner.invoke(app, [
        "dataset", "generate-unanswerable", 
        "--dataset", str(DATA_ROOT / "garage" / "dev"),
        "--ratio", "0.25",
        "--output", str(out_file)
    ])
    
    assert result.exit_code == 0
    assert "Generating synthetic unanswerable queries" in result.stdout
    assert "ratio=0.25" in result.stdout
    assert out_file.exists()


def setup_module(module):
    """Setup test datasets dir before tests."""
    if DATA_ROOT.exists():
        shutil.rmtree(DATA_ROOT)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)


def teardown_module(module):
    """Clean up test dataset directories after tests run."""
    if DATA_ROOT.exists():
        shutil.rmtree(DATA_ROOT)
    if Path("data/synth_out.jsonl").exists():
        Path("data/synth_out.jsonl").unlink()

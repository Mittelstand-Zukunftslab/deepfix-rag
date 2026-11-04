"""Unit tests for unknown keys, the gate, index manifests, and schema guards."""

import json
from pathlib import Path

from pydantic import ValidationError
import pytest
from typer.testing import CliRunner

from deepfix_rag.cli.ui import EXIT_SUCCESS, EXIT_VALIDATION_ERROR
from deepfix_rag.config.mutator import UnknownKeyError, apply_mutation
from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.data import QARecord
from deepfix_rag.evaluation.runner import resolve_eval_records
from deepfix_rag.gate import GateInputError, judge_compare
from deepfix_rag.index_manifest import (
    StaleIndexError,
    assert_index_current,
    manifest_matches,
    write_index_manifest,
)
from deepfix_rag.main import app
from deepfix_rag.optimization import _record_ledger
from deepfix_rag.promotion import _rejection

pytestmark = [pytest.mark.unit]


def _questions(score: float, count: int = 20) -> list[dict]:
    return [
        {"query_id": str(index), "metrics": {"ndcg": score, "recall": score, "precision": score}}
        for index in range(count)
    ]


def _compare(candidate_score: float, baseline_score: float, **extra: object) -> dict:
    payload = {
        "candidate": {
            "ndcg": candidate_score,
            "recall": candidate_score,
            "precision": candidate_score,
            "errors": 0,
            "judge_failures": 0,
            "configured_backend": "hybrid",
            "effective_backend": "hybrid",
            "questions": _questions(candidate_score),
        },
        "baseline": {
            "ndcg": baseline_score,
            "recall": baseline_score,
            "precision": baseline_score,
            "errors": 0,
            "judge_failures": 0,
            "configured_backend": "hybrid",
            "effective_backend": "hybrid",
            "questions": _questions(baseline_score),
        },
        "delta": {
            "ndcg": candidate_score - baseline_score,
            "recall": candidate_score - baseline_score,
            "precision": candidate_score - baseline_score,
        },
    }
    payload.update(extra)
    return payload


def test_unknown_override_raises_with_a_suggestion(default_rag_config: RAGConfig) -> None:
    with pytest.raises(UnknownKeyError) as raised:
        apply_mutation(default_rag_config, {"rerank.top_nn": 3})
    assert raised.value.path == "rerank.top_nn"
    assert "rerank.top_n" in raised.value.suggestions


def test_mutate_config_unknown_key_exits_1(
    cli_runner: CliRunner, default_config_path: Path, tmp_path: Path
) -> None:
    output = tmp_path / "mutated.yaml"
    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(default_config_path),
            "--set",
            "embedding.model=foo",
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "unknown_key"
    assert payload["path"] == "embedding.model"
    assert output.exists() is False


def test_mutate_config_reports_index_time_change(
    cli_runner: CliRunner, default_config_path: Path, tmp_path: Path
) -> None:
    output = tmp_path / "mutated.yaml"
    result = cli_runner.invoke(
        app,
        [
            "mutate-config",
            "--base",
            str(default_config_path),
            "--set",
            "chunking.size=2048",
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["ignored_keys"] == []
    assert payload["index_time_changed"] is True
    assert payload["schema_version"]
    assert payload["diffs"]["chunking.size"]["after"] == 2048


def test_rerank_window_and_expansion_cap_and_language() -> None:
    with pytest.raises(ValidationError):
        RAGConfig.model_validate(
            {"rerank": {"enabled": True, "top_n": 20}, "retrieval": {"top_k": 10}}
        )
    with pytest.raises(ValidationError):
        RAGConfig.model_validate(
            {
                "rerank": {"enabled": True, "top_n": 3},
                "retrieval": {"top_k": 10},
                "eval": {"cutoff_k": 5},
            }
        )
    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"query_expansion_num": 100}})
    with pytest.raises(ValidationError):
        RAGConfig.model_validate({"retrieval": {"language": "not-a-language"}})
    kept = RAGConfig.model_validate(
        {
            "rerank": {"enabled": True, "top_n": 5},
            "retrieval": {"top_k": 10, "query_expansion_num": 8, "language": "english"},
        }
    )
    assert kept.retrieval.query_expansion_num == 8
    assert kept.retrieval.language == "english"


def test_from_yaml_missing_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        RAGConfig.from_yaml(missing)
    loaded = RAGConfig.from_yaml("version: '1.0'\nchunking:\n  size: 128\n")
    assert loaded.chunking.size == 128


def test_gate_keeps_a_clear_paired_gain() -> None:
    decision = judge_compare(_compare(0.8, 0.4), "ndcg", min_delta=0.025)
    assert decision["pass"] is True
    assert decision["decision"] == "keep"
    assert decision["wins"] == 20
    assert decision["losses"] == 0
    assert decision["ties"] == 0
    assert decision["ci95"][0] > 0
    assert decision["primary_delta"] == pytest.approx(0.4)


def test_gate_reverts_a_no_op_and_a_backend_fallback() -> None:
    noop = judge_compare(_compare(0.5, 0.5), "ndcg", min_delta=0.025)
    assert noop["pass"] is False
    assert noop["decision"] == "revert"
    assert noop["wins"] == 0
    assert noop["losses"] == 0
    assert noop["ties"] == 20
    assert noop["ci95"][0] <= 0

    fallen = _compare(0.8, 0.4)
    fallen["candidate"]["effective_backend"] = "dense"
    rejected = judge_compare(fallen, "ndcg", min_delta=0.025)
    assert rejected["pass"] is False
    assert any("effective backend" in reason for reason in rejected["reasons"])


def test_gate_allows_a_small_secondary_drop() -> None:
    payload = _compare(0.8, 0.4)
    payload["delta"]["recall"] = -0.005
    assert judge_compare(payload, "ndcg", min_delta=0.025, secondary_tolerance=-0.01)["pass"]
    payload["delta"]["recall"] = -0.02
    rejected = judge_compare(payload, "ndcg", min_delta=0.025, secondary_tolerance=-0.01)
    assert rejected["pass"] is False
    assert rejected["regressed"] == ["recall"]


def test_gate_cli_reads_compare_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "compare.json"
    path.write_text(json.dumps(_compare(0.8, 0.4)), encoding="utf-8")
    result = cli_runner.invoke(
        app,
        ["gate", "--compare", str(path), "--primary", "ndcg", "--json"],
    )
    assert result.exit_code == EXIT_SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["pass"] is True
    assert payload["min_delta"] == pytest.approx(0.025)


def test_gate_rejects_a_compare_file_without_questions() -> None:
    with pytest.raises(GateInputError):
        judge_compare({"candidate": {"ndcg": 1}, "baseline": {"ndcg": 0}, "delta": {}}, "ndcg")


def test_holdout_no_op_and_tie_are_rejected() -> None:
    assert _rejection({"ndcg": 0.05}, {"ndcg": 0.0}, "ndcg", 0.025, 0, 0)
    tie = _rejection({"ndcg": 0.05}, {"ndcg": 0.01}, "ndcg", 0.025, 2, 2)
    assert tie is not None
    assert "win more" in tie
    assert (
        _rejection({"ndcg": 0.05, "recall": -0.005}, {"ndcg": 0.02}, "ndcg", 0.025, 4, 1) is None
    )
    dropped = _rejection({"ndcg": 0.05, "recall": -0.02}, {"ndcg": 0.02}, "ndcg", 0.025, 4, 1)
    assert dropped is not None
    assert "recall" in dropped


def test_index_manifest_mismatch_is_stale(tmp_path: Path, sample_corpus_path: Path) -> None:
    index_dir = tmp_path / "idx"
    config = RAGConfig()
    config.data.path_or_repo = str(sample_corpus_path)
    config.retrieval.lancedb_index_dir = str(index_dir)
    write_index_manifest(index_dir, config)
    assert manifest_matches(index_dir, config)
    changed = config.model_copy(
        update={"chunking": config.chunking.model_copy(update={"size": config.chunking.size + 64})}
    )
    with pytest.raises(StaleIndexError):
        assert_index_current(index_dir, changed)
    empty = tmp_path / "missing"
    assert_index_current(empty, config)
    occupied = tmp_path / "old"
    occupied.mkdir()
    (occupied / "marker").write_text("old", encoding="utf-8")
    with pytest.raises(StaleIndexError):
        assert_index_current(occupied, config)


def test_index_if_changed_skips_without_a_rebuild(
    cli_runner: CliRunner, tmp_path: Path, sample_corpus_path: Path
) -> None:
    index_dir = tmp_path / "idx"
    config = RAGConfig()
    config.data.path_or_repo = str(sample_corpus_path)
    config.retrieval.lancedb_index_dir = str(index_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config.to_yaml(), encoding="utf-8")
    write_index_manifest(index_dir, config)
    result = cli_runner.invoke(
        app,
        [
            "index",
            "--config",
            str(config_path),
            "--index-dir",
            str(index_dir),
            "--if-changed",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["skipped"] is True
    assert payload["signature_hash"]


def test_eval_stale_index_exits_1_without_scoring(
    cli_runner: CliRunner, tmp_path: Path, sample_qa_path: Path, sample_corpus_path: Path
) -> None:
    index_dir = tmp_path / "idx"
    index_dir.mkdir()
    (index_dir / "documents.lance").write_text("not-a-real-index", encoding="utf-8")
    config = RAGConfig()
    config.data.path_or_repo = str(sample_corpus_path)
    config.retrieval.lancedb_index_dir = str(index_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config.to_yaml(), encoding="utf-8")
    result = cli_runner.invoke(
        app,
        [
            "eval",
            "--config",
            str(config_path),
            "--dataset",
            str(sample_qa_path),
            "--index-dir",
            str(index_dir),
            "--retrieval-only",
            "--no-mlflow",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR, result.output
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "stale_index"


def test_schema_json_lists_index_time_keys(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["schema", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["schema_version"]
    assert "chunking.*" in payload["index_time_keys"]
    assert "retrieval.backend" in payload["index_time_keys"]
    assert "properties" in payload["json_schema"]
    assert "model" not in payload["json_schema"]["$defs"]["EmbeddingConfig"]["properties"]


def test_seeded_limit_is_stable_and_not_the_prefix() -> None:
    records = [
        QARecord(query_id=str(index), query=f"q{index}", doc_ids=[f"d{index}"])
        for index in range(12)
    ]
    first = resolve_eval_records(None, records, limit=4, seed=0)
    again = resolve_eval_records(None, records, limit=4, seed=0)
    other = resolve_eval_records(None, records, limit=4, seed=1)
    prefix = resolve_eval_records(None, records, limit=4)
    assert [row.query_id for row in first] == [row.query_id for row in again]
    assert len(first) == 4
    assert [row.query_id for row in first] != [row.query_id for row in other]
    assert [row.query_id for row in prefix] == ["0", "1", "2", "3"]


def test_ledger_row_records_the_skill_and_omits_holdout(tmp_path: Path) -> None:
    from deepfix_rag.optimization import init_optimization
    from deepfix_rag.storage import connect

    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (workspace / "configs").mkdir()
    (workspace / "configs" / "champion.yaml").write_text("version: '1.0'\n", encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps({"step": 2, "skill": "rerank-calibration", "keys": ["rerank.enabled"]}),
        encoding="utf-8",
    )
    db_path = tmp_path / "product.db"
    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at, ended_at, primary_metric, dev_delta
            )
            VALUES ('run-1', 'rejected', 'manual', 'snap', 1, 1, 1, '[]', 't', 't', 'ndcg', ?)
            """,
            (json.dumps({"ndcg": 0.01}),),
        )
    _record_ledger(db_path, workspace, "run-1")
    row = json.loads((workspace / "runs" / "ledger.jsonl").read_text(encoding="utf-8"))
    assert row["skill"] == "rerank-calibration"
    assert row["keys"] == ["rerank.enabled"]
    assert row["decision"] == "rejected"
    assert row["dev_delta"]["ndcg"] == pytest.approx(0.01)
    assert row["champion_sha256"]
    assert "holdout" not in row

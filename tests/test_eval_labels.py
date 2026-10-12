"""Multi-label eval records, judged-only ranking, and in-memory scoring."""

import json
from pathlib import Path

import pytest

from deepfix_rag.cli.eval import metric_delta
from deepfix_rag.cli.ui import EXIT_VALIDATION_ERROR
from deepfix_rag.config.schema import GenerationMetricType
from deepfix_rag.data import QARecord, QAValidationError, qa_record_from_row
from deepfix_rag.evaluation.labels import (
    generation_metrics_for_record,
    judged_retrieval_metrics,
)
from deepfix_rag.evaluation.runner import (
    CaseResult,
    EvaluationSummary,
    classify_case_failure,
    load_qa_dataset,
    resolve_eval_records,
)
from deepfix_rag.main import app

pytestmark = pytest.mark.unit


def test_single_doc_id_loads_as_one_relevant_document(sample_qa_path: Path) -> None:
    records = load_qa_dataset(sample_qa_path)
    assert records
    for record in records:
        assert record.doc_ids == [record.doc_id]
        assert record.negative_doc_ids is None
        assert record.answer
        assert record.unanswerable is False


def test_multi_doc_ids_and_missing_answer() -> None:
    record = qa_record_from_row(
        {
            "query_id": "q",
            "query": "Which documents apply?",
            "doc_ids": ["a", "b", "a"],
        }
    )
    assert record.doc_ids == ["a", "b"]
    assert record.doc_id == "a"
    assert record.answer is None
    assert record.negative_doc_ids is None
    assert record.unanswerable is False


def test_both_document_keys_are_rejected() -> None:
    with pytest.raises(QAValidationError, match="both doc_id and doc_ids"):
        qa_record_from_row({"query_id": "q", "query": "q?", "doc_id": "a", "doc_ids": ["a"]})


def test_overlap_between_relevant_and_negative_is_rejected() -> None:
    with pytest.raises(QAValidationError, match="relevant and negative"):
        qa_record_from_row(
            {
                "query_id": "q",
                "query": "q?",
                "doc_ids": ["a"],
                "negative_doc_ids": ["a", "b"],
            }
        )


def test_negative_only_row_is_not_unanswerable() -> None:
    record = qa_record_from_row(
        {"query_id": "q", "query": "q?", "doc_ids": [], "negative_doc_ids": ["n"]}
    )
    assert record.doc_ids == []
    assert record.answer is None
    assert record.unanswerable is False
    case = CaseResult(
        index=0,
        query_id=record.query_id,
        question=record.query,
        ground_truth_doc_ids=record.doc_ids,
        unanswerable=record.unanswerable,
    )
    assert classify_case_failure(case).value != "deflection_failure"


def test_empty_ground_truth_without_negatives_stays_unanswerable() -> None:
    record = qa_record_from_row({"query_id": "q", "query": "q?", "answer": "no", "doc_id": ""})
    assert record.unanswerable is True
    assert record.doc_ids == []


def test_missing_answer_skips_reference_metrics_only() -> None:
    configured = [
        GenerationMetricType.FAITHFULNESS,
        GenerationMetricType.CONTEXT_PRECISION,
        GenerationMetricType.CONTEXT_RECALL,
        GenerationMetricType.ANSWER_RELEVANCY,
    ]
    assert generation_metrics_for_record(configured, None) == [
        GenerationMetricType.FAITHFULNESS,
        GenerationMetricType.ANSWER_RELEVANCY,
    ]
    assert generation_metrics_for_record(configured, "A reference answer.") == configured


def test_retrieved_negative_lowers_precision_and_ndcg() -> None:
    relevant = ["rel"]
    negatives = ["neg"]
    with_negative = judged_retrieval_metrics(
        ["neg", "other", "rel"], relevant, negatives, cutoff=10
    )
    without_negative = judged_retrieval_metrics(["other", "rel"], relevant, [], cutoff=10)
    assert with_negative["precision"] == 0.5
    assert without_negative["precision"] == 1.0
    assert with_negative["ndcg"] < without_negative["ndcg"]
    assert with_negative["recall"] == 1.0


def test_unjudged_hit_is_left_out_of_precision() -> None:
    scored = judged_retrieval_metrics(["other", "rel"], ["rel"], ["neg"], cutoff=10)
    assert scored["precision"] == 1.0
    assert scored["ndcg"] == 1.0


def test_empty_relevant_list_omits_recall() -> None:
    scored = judged_retrieval_metrics(["neg"], [], ["neg"], cutoff=10)
    assert scored["recall"] is None
    assert scored["precision"] == 0.0


def test_legacy_constructor_still_has_one_relevant_document() -> None:
    record = QARecord(query_id="q1", query="Q1?", answer="A1", doc_id="d1")
    assert record.doc_ids == ["d1"]
    assert record.negative_doc_ids is None


def test_in_memory_records_score_like_the_same_file(tmp_path: Path) -> None:
    row = {
        "query_id": "fb_1",
        "query": "Where is the rule?",
        "doc_ids": ["a", "b"],
        "negative_doc_ids": ["c"],
    }
    path = tmp_path / "feedback.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    from_file = resolve_eval_records(path, None)
    from_memory = resolve_eval_records(None, [qa_record_from_row(row)])
    assert from_file == from_memory
    retrieved = ["c", "z", "a"]
    assert judged_retrieval_metrics(
        retrieved, from_file[0].doc_ids, from_file[0].negative_doc_ids or [], 10
    ) == judged_retrieval_metrics(
        retrieved, from_memory[0].doc_ids, from_memory[0].negative_doc_ids or [], 10
    )


def test_skipped_metric_is_absent_from_comparison_delta() -> None:
    summary = EvaluationSummary(total=1, scored=1, errors=0, metrics={"ndcg": 0.4}, rows=[])
    payload = summary.to_dict()
    assert "context_precision" not in payload
    delta = metric_delta(payload, payload)
    assert "ndcg" in delta
    assert delta["ndcg"] == 0.0
    assert "context_precision" not in delta
    assert "faithfulness" not in delta


def test_questions_include_only_scored_metrics() -> None:
    summary = EvaluationSummary(
        total=1,
        scored=1,
        errors=0,
        rows=[{"query_id": "q", "ndcg": 0.5, "recall": None, "question": "q?"}],
    )
    questions = summary.to_dict()["questions"]
    assert questions == [{"query_id": "q", "metrics": {"ndcg": 0.5}}]


def test_eval_cli_rejects_both_document_keys(
    cli_runner, default_config_path: Path, tmp_path: Path
) -> None:
    dataset = tmp_path / "qa.jsonl"
    dataset.write_text(
        json.dumps({"query_id": "q", "query": "q?", "doc_id": "a", "doc_ids": ["b"]}) + "\n",
        encoding="utf-8",
    )
    result = cli_runner.invoke(
        app,
        [
            "eval",
            "--config",
            str(default_config_path),
            "--dataset",
            str(dataset),
            "--retrieval-only",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_VALIDATION_ERROR
    payload = json.loads(result.stdout)
    assert payload["code"] == EXIT_VALIDATION_ERROR
    assert "both doc_id and doc_ids" in payload["error"]

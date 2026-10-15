"""Passage votes become a dev/holdout dataset. Holdout is never written down."""

import json
from pathlib import Path

import mlflow
import pytest

from deepfix_rag.accounts import create_admin, init_db
from deepfix_rag.documents import init_documents
from deepfix_rag.feedback import (
    build_questions,
    export_feedback,
    is_holdout,
    normalize_query,
    query_id_for,
    record_vote,
)
from deepfix_rag.interactions import record_interaction
from deepfix_rag.pipeline import RetrievalResult

pytestmark = pytest.mark.unit


def _user(db_path: Path, email: str) -> str:
    init_db(db_path)
    return create_admin(db_path, email, "correct-horse").id


def _ask(
    db_path: Path,
    user_id: str,
    query: str,
    hits: list[tuple[str, str]],
    *,
    answer: str | None = "an answer that must not become a label",
) -> str:
    stored = record_interaction(
        db_path,
        kind="ask",
        user_id=user_id,
        query=query,
        build_id="build",
        config_path="champion.yaml",
        results=[RetrievalResult(doc_id=doc_id, score=0.2, text=text) for doc_id, text in hits],
        answer=answer,
    )
    return stored.interaction_id


def _vote(db_path: Path, user_id: str, interaction_id: str, rank: int, action: str) -> None:
    record_vote(
        db_path,
        user_id=user_id,
        interaction_id=interaction_id,
        target="passage",
        rank=rank,
        action=action,
    )


def _pair() -> tuple[str, str]:
    dev = holdout = None
    index = 0
    while dev is None or holdout is None:
        text = f"question {index}"
        normalized = normalize_query(text)
        if is_holdout(normalized) and holdout is None:
            holdout = text
        elif not is_holdout(normalized) and dev is None:
            dev = text
        index += 1
    return dev, holdout


def test_net_votes_roll_up_to_documents_and_skip_holdout_files(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "product.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    ada = _user(db_path, "ada@example.com")
    bea = _user(db_path, "bea@example.com")
    dev_query, holdout_query = _pair()
    same = _ask(
        db_path,
        ada,
        dev_query,
        [("doc-a", "alpha passage"), ("doc-a", "beta passage"), ("doc-b", "only down")],
    )
    _vote(db_path, ada, same, 1, "up")
    _vote(db_path, ada, same, 2, "down")
    _vote(db_path, ada, same, 3, "down")
    tied = _ask(db_path, ada, f"{dev_query} tied", [("doc-c", "tied passage")])
    _vote(db_path, ada, tied, 1, "up")
    _vote(db_path, bea, tied, 1, "down")
    negative_only = _ask(db_path, ada, f"{dev_query} negative", [("doc-d", "negative passage")])
    _vote(db_path, ada, negative_only, 1, "down")
    unlabeled = _ask(
        db_path,
        ada,
        f"{dev_query} unlabeled",
        [("doc-e", "champion top hit that nobody voted")],
    )
    assert unlabeled
    deleted_question = _ask(
        db_path, ada, f"{dev_query} deleted", [("doc-gone", "removed passage")]
    )
    _vote(db_path, ada, deleted_question, 1, "up")
    init_documents(db_path)
    from deepfix_rag.accounts import connect

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, filename, media_type, text, status, uploaded_by, created_at, deleted_at
            )
            VALUES ('doc-gone', 'gone.txt', 'text/plain', 'removed passage', 'deleting', ?, 't', 't')
            """,
            (ada,),
        )
    holdout_id = _ask(db_path, ada, holdout_query, [("doc-h", "holdout passage")])
    _vote(db_path, ada, holdout_id, 1, "up")
    record_vote(
        db_path,
        user_id=ada,
        interaction_id=same,
        target="answer",
        action="down",
    )

    questions = {question.query: question for question in build_questions(db_path)}
    mixed = questions[normalize_query(dev_query)]
    assert mixed.doc_ids == ["doc-a"]
    assert mixed.negative_doc_ids == ["doc-b"]
    assert "doc-e" not in mixed.doc_ids
    assert normalize_query(f"{dev_query} tied") not in questions
    negative = questions[normalize_query(f"{dev_query} negative")]
    assert negative.doc_ids == []
    assert negative.negative_doc_ids == ["doc-d"]
    assert normalize_query(f"{dev_query} deleted") not in questions
    assert normalize_query(f"{dev_query} unlabeled") not in questions
    holdout = questions[normalize_query(holdout_query)]
    assert holdout.holdout is True
    assert holdout.query_id == query_id_for(normalize_query(holdout_query))
    assert is_holdout(normalize_query(holdout_query)) is is_holdout(normalize_query(holdout_query))

    exported = export_feedback(db_path, workspace)
    again = export_feedback(db_path, workspace)
    first_split = {
        question.query_id: question.holdout for question in exported.dev + exported.holdout
    }
    second_split = {question.query_id: question.holdout for question in again.dev + again.holdout}
    assert first_split == second_split
    assert exported.holdout
    assert all(not question.holdout for question in exported.dev)
    written = list((workspace / "feedback").rglob("*"))
    assert written
    for path in written:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert holdout.query_id not in text
        assert normalize_query(holdout_query) not in text
        if path.name == "feedback_dev.jsonl":
            for line in text.splitlines():
                row = json.loads(line)
                assert "answer" not in row
                assert "doc_ids" in row
                assert "negative_doc_ids" in row

"""Promotion scores a hand-written candidate and does not start OpenHands."""

import asyncio
import json
from pathlib import Path

from llama_index.core.embeddings import MockEmbedding
import mlflow
import pytest

from deepfix_rag.accounts import create_admin
from deepfix_rag.config.schema import DataSourceType, RAGConfig, RetrievalBackend
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.feedback import is_holdout, normalize_query, query_id_for, record_vote
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.interactions import record_interaction
from deepfix_rag.optimization import finish_run, get_run, init_optimization
from deepfix_rag.pipeline import RetrievalResult
from deepfix_rag.promotion import question_wins

pytestmark = pytest.mark.integration


class BoomEmbedding(MockEmbedding):
    """Embedding model that fails every text embedding."""

    def _get_text_embedding(self, text: str) -> list[float]:
        raise RuntimeError("embed failed")


def test_question_wins_count_strict_differences() -> None:
    wins, losses = question_wins(
        [
            {"query_id": "a", "ndcg": 1.0},
            {"query_id": "b", "ndcg": 0.2},
            {"query_id": "c", "ndcg": 0.4},
        ],
        [
            {"query_id": "a", "ndcg": 0.0},
            {"query_id": "b", "ndcg": 0.8},
            {"query_id": "c", "ndcg": 0.4},
        ],
        "ndcg",
    )
    assert (wins, losses) == (1, 1)


def test_invalid_backend_is_rejected_without_replacing_the_champion(
    tmp_path: Path,
    default_config_path: Path,
) -> None:
    workspace, db_path, _coordinator = _ready(tmp_path, default_config_path, embed=False)
    champion = workspace / "configs" / "champion.yaml"
    before = champion.read_text(encoding="utf-8")
    candidate = _write_candidate(workspace, champion, backend=RetrievalBackend.GRAPH)
    _running(db_path, "run-graph")
    _result(workspace, "run-graph", candidate, "ndcg")
    finish_run(db_path, workspace, "run-graph", runner_error=None, timed_out=False)
    stored = get_run(db_path, "run-graph")
    assert stored.status == "rejected"
    assert stored.reason == "The candidate uses a retrieval backend that is not allowed."
    assert champion.read_text(encoding="utf-8") == before


def test_query_time_tie_is_rejected_and_index_time_gain_is_promoted(
    tmp_path: Path,
    default_config_path: Path,
    sample_corpus_path: Path,
) -> None:
    _tracking(tmp_path)
    workspace, db_path, coordinator = _ready(tmp_path, default_config_path, sample_corpus_path)
    dev_query, holdout_query = _labeled_pair(db_path, coordinator)
    _dev_snapshot(workspace, db_path, dev_query)
    champion = workspace / "configs" / "champion.yaml"
    _set_top_k(champion, 1)
    before = champion.read_text(encoding="utf-8")
    pointer = coordinator.serving_build_id()
    same = _write_candidate(workspace, champion, top_k=1)
    _running(db_path, "tie")
    _result(workspace, "tie", same, "ndcg")
    finish_run(
        db_path,
        workspace,
        "tie",
        runner_error=None,
        timed_out=False,
        coordinator=coordinator,
        min_primary_delta=0.025,
    )
    rejected = get_run(db_path, "tie")
    assert rejected.status == "rejected"
    assert rejected.reason is not None
    assert "0.000" in rejected.reason
    assert "0.025" in rejected.reason
    assert champion.read_text(encoding="utf-8") == before
    assert coordinator.serving_build_id() == pointer

    better = _write_candidate(workspace, champion, top_k=5, chunk_size=512)
    _running(db_path, "gain")
    _result(workspace, "gain", better, "ndcg")
    finish_run(
        db_path,
        workspace,
        "gain",
        runner_error=None,
        timed_out=False,
        coordinator=coordinator,
        min_primary_delta=0.025,
    )
    promoted = get_run(db_path, "gain")
    assert promoted.status == "promoted"
    assert promoted.dev_delta is not None
    assert promoted.dev_delta["ndcg"] >= 0.025
    assert promoted.holdout_delta is not None
    assert promoted.holdout_delta["ndcg"] >= 0
    assert promoted.holdout_wins is not None
    assert promoted.holdout_wins >= (promoted.holdout_losses or 0)
    assert "top_k: 5" in champion.read_text(encoding="utf-8")
    assert list((workspace / "configs" / "history").glob("*.yaml"))
    assert coordinator.serving_build_id() != pointer
    lease = coordinator.checkout()
    assert lease is not None
    assert lease.service.config.retrieval.top_k == 5
    coordinator.release(lease)
    _assert_holdout_unwritten(tmp_path, workspace, holdout_query)


def test_holdout_regression_rejects_the_candidate(
    tmp_path: Path,
    default_config_path: Path,
    sample_corpus_path: Path,
) -> None:
    _tracking(tmp_path)
    workspace, db_path, coordinator = _ready(tmp_path, default_config_path, sample_corpus_path)
    dev_query, holdout_query = _labeled_pair(db_path, coordinator)
    _dev_snapshot(workspace, db_path, dev_query)
    champion = workspace / "configs" / "champion.yaml"
    _set_top_k(champion, 1)
    before = champion.read_text(encoding="utf-8")
    pointer = coordinator.serving_build_id()
    candidate = _write_candidate(workspace, champion, top_k=5)
    _running(db_path, "holdout")
    _result(workspace, "holdout", candidate, "precision")
    finish_run(
        db_path,
        workspace,
        "holdout",
        runner_error=None,
        timed_out=False,
        coordinator=coordinator,
        min_primary_delta=0.025,
    )
    stored = get_run(db_path, "holdout")
    assert stored.status == "rejected"
    assert stored.reason is not None
    assert stored.reason.startswith("The holdout precision delta is")
    assert champion.read_text(encoding="utf-8") == before
    assert coordinator.serving_build_id() == pointer
    _assert_holdout_unwritten(tmp_path, workspace, holdout_query)


def test_a_failed_candidate_build_keeps_the_champion(
    tmp_path: Path,
    default_config_path: Path,
    sample_corpus_path: Path,
) -> None:
    workspace, db_path, coordinator = _ready(
        tmp_path,
        default_config_path,
        sample_corpus_path,
        embed_model=BoomEmbedding(embed_dim=16),
        build=False,
    )
    champion = workspace / "configs" / "champion.yaml"
    before = champion.read_text(encoding="utf-8")
    candidate = _write_candidate(workspace, champion, chunk_size=512)
    _running(db_path, "boom")
    _result(workspace, "boom", candidate, "ndcg")
    finish_run(
        db_path,
        workspace,
        "boom",
        runner_error=None,
        timed_out=False,
        coordinator=coordinator,
    )
    stored = get_run(db_path, "boom")
    assert stored.status == "failed"
    assert stored.reason == "embed failed"
    assert champion.read_text(encoding="utf-8") == before
    assert coordinator.serving_build_id() is None


def _tracking(tmp_path: Path) -> None:
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")


def _ready(
    tmp_path: Path,
    default_config_path: Path,
    sample_corpus_path: Path | None = None,
    *,
    embed: bool = True,
    embed_model: MockEmbedding | None = None,
    build: bool = True,
) -> tuple[Path, Path, IndexCoordinator | None]:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "private" / "product.db"
    prepare_workspace(workspace, default_config_path)
    if sample_corpus_path is not None:
        _install_corpus(workspace, sample_corpus_path)
    champion = workspace / "configs" / "champion.yaml"
    _write_bm25(champion, workspace, top_k=10)
    model = embed_model or MockEmbedding(embed_dim=16)
    coordinator = IndexCoordinator(workspace, db_path, embed_model=model) if embed else None
    if coordinator is not None and build:
        coordinator.startup()
        coordinator.wait()
    return workspace, db_path, coordinator


def _install_corpus(workspace: Path, sample_corpus_path: Path) -> None:
    corpus = workspace / "corpus"
    for line in sample_corpus_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        item["filename"] = f"{item['doc_id']}.txt"
        (corpus / f"{item['doc_id']}.json").write_text(
            json.dumps(item, ensure_ascii=False),
            encoding="utf-8",
        )


def _write_bm25(path: Path, workspace: Path, *, top_k: int, chunk_size: int = 1024) -> None:
    config = RAGConfig.from_yaml(path)
    config = config.model_copy(
        update={
            "retrieval_only": True,
            "chunking": config.chunking.model_copy(update={"size": chunk_size}),
            "retrieval": config.retrieval.model_copy(
                update={
                    "backend": RetrievalBackend.BM25,
                    "top_k": top_k,
                    "query_expansion_num": 0,
                    "lancedb_index_dir": str(workspace / "indexes" / "current"),
                }
            ),
            "data": config.data.model_copy(
                update={
                    "path_or_repo": str(workspace / "corpus"),
                    "source_type": DataSourceType.DIRECTORY,
                }
            ),
            "eval": config.eval.model_copy(update={"cutoff_k": 5}),
        }
    )
    path.write_text(config.to_yaml(), encoding="utf-8")


def _set_top_k(path: Path, top_k: int) -> None:
    config = RAGConfig.from_yaml(path)
    updated = config.model_copy(
        update={"retrieval": config.retrieval.model_copy(update={"top_k": top_k})}
    )
    path.write_text(updated.to_yaml(), encoding="utf-8")


def _write_candidate(
    workspace: Path,
    champion: Path,
    *,
    top_k: int | None = None,
    chunk_size: int | None = None,
    backend: RetrievalBackend | None = None,
) -> Path:
    config = RAGConfig.from_yaml(champion)
    retrieval = config.retrieval.model_copy(
        update={
            "top_k": config.retrieval.top_k if top_k is None else top_k,
            "backend": config.retrieval.backend if backend is None else backend,
        }
    )
    chunking = config.chunking.model_copy(
        update={"size": config.chunking.size if chunk_size is None else chunk_size}
    )
    target = workspace / "runs" / "candidate.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        config.model_copy(update={"retrieval": retrieval, "chunking": chunking}).to_yaml(),
        encoding="utf-8",
    )
    return target


def _labeled_pair(db_path: Path, coordinator: IndexCoordinator | None) -> tuple[str, str]:
    assert coordinator is not None
    dev_query = _query_with_split(False)
    holdout_query = _query_with_split(True)
    dev_docs = _ranked_docs(coordinator, dev_query)
    holdout_docs = _ranked_docs(coordinator, holdout_query)
    assert len(dev_docs) >= 2
    assert len(holdout_docs) >= 2
    # Dev relevant sits at rank 2, so a larger top_k is a gain.
    _vote(db_path, dev_query, negative=dev_docs[0], relevant=dev_docs[1])
    # Holdout relevant sits at rank 1, so a larger top_k cannot raise nDCG
    # and lowers precision once the next judged document is included.
    _vote(db_path, holdout_query, negative=holdout_docs[1], relevant=holdout_docs[0])
    return dev_query, holdout_query


def _query_with_split(holdout: bool) -> str:
    base = "straßenverkehr alkoholischer getränke"
    for suffix in range(400):
        query = normalize_query(f"{base} marker{suffix}")
        if is_holdout(query) is holdout:
            return query
    raise AssertionError("No query landed in the requested split.")


def _ranked_docs(coordinator: IndexCoordinator, query: str) -> list[str]:
    lease = coordinator.checkout()
    assert lease is not None
    try:
        results = asyncio.run(lease.service.retrieve(query))
    finally:
        coordinator.release(lease)
    ordered: list[str] = []
    for result in results:
        if result.doc_id not in ordered:
            ordered.append(result.doc_id)
    return ordered


def _vote(db_path: Path, query: str, *, relevant: str, negative: str) -> None:
    user = create_admin(db_path, f"{query_id_for(query)}@example.com", "correct-horse")
    stored = record_interaction(
        db_path,
        kind="retrieval",
        user_id=user.id,
        query=query,
        build_id="build",
        config_path="configs/champion.yaml",
        results=[
            RetrievalResult(doc_id=negative, score=1.0, text="negative passage"),
            RetrievalResult(doc_id=relevant, score=0.5, text="relevant passage"),
        ],
    )
    record_vote(
        db_path,
        user_id=user.id,
        interaction_id=stored.interaction_id,
        target="passage",
        action="down",
        rank=1,
    )
    record_vote(
        db_path,
        user_id=user.id,
        interaction_id=stored.interaction_id,
        target="passage",
        action="up",
        rank=2,
    )


def _dev_snapshot(workspace: Path, db_path: Path, query: str) -> None:
    from deepfix_rag.feedback import build_questions

    questions = []
    for question in build_questions(db_path):
        if question.query == query and not question.holdout:
            questions.append(question.row())
    assert questions
    directory = workspace / "feedback" / "snap"
    directory.mkdir(parents=True)
    (directory / "feedback_dev.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in questions),
        encoding="utf-8",
    )


def _running(db_path: Path, run_id: str) -> None:
    from deepfix_rag.accounts import connect

    init_optimization(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_runs (
                run_id, status, trigger_kind, snapshot_id, dev_count, new_dev_count,
                holdout_count, dev_query_ids, started_at
            )
            VALUES (?, 'running', 'manual', 'snap', 1, 1, 1, '[]', 't')
            """,
            (run_id,),
        )


def _result(workspace: Path, run_id: str, candidate: Path, metric: str) -> None:
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "candidate_config_path": str(candidate),
                "primary_metric": metric,
                "summary": "Raised top_k.",
                "mlflow_run_id": "agent-mlflow",
            }
        ),
        encoding="utf-8",
    )


def _assert_holdout_unwritten(tmp_path: Path, workspace: Path, holdout_query: str) -> None:
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert holdout_query not in text
    mlflow_db = tmp_path / "mlflow.db"
    if mlflow_db.is_file():
        assert holdout_query not in mlflow_db.read_text(encoding="utf-8", errors="ignore")

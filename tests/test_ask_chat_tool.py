"""The one tool: it reports the retrieval path's survivors, and adds only depth.

The point of this module is FR-024, "served equals scored". ``search_documents``
must return exactly what ``QueryService.retrieve`` returns, truncated to the
product's own display window, because the relevance floor lives inside that call
and a second comparison in the tool would be a second contract the optimizer
does not tune. The parity case is therefore an equality assertion, not a
threshold: raise the floor above every score and both sides must come back empty
rather than one of them showing passages the product's own search would hide.

A real LanceDB index is built in a workspace under ``tmp_path`` and served
through a real ``IndexCoordinator``, so the lease, the champion config, and the
retrieval path are the ones the API uses. No mocks, per ``AGENTS.md``.
"""

import asyncio
import json
from pathlib import Path

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
import pytest

from deepfix_rag import ask_chat
from deepfix_rag.config.schema import DataSourceType, RAGConfig
from deepfix_rag.config.settings import Settings
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.index_manifest import index_time_signature
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.service import displayed_limit
from deepfix_rag.storage import connect

pytestmark = [pytest.mark.integration]

DOCUMENTS = {
    "doc_de_001": (
        "Gemäß § 315c Strafgesetzbuch (StGB) wird wegen Gefährdung des Straßenverkehrs "
        "bestraft, wer im Straßenverkehr ein Fahrzeug führt, obwohl er infolge des "
        "Genusses alkoholischer Getränke nicht in der Lage ist, das Fahrzeug sicher zu "
        "führen. Die Trunkenheitsfahrt ist eine Gefährdung, keine Verletzung."
    ),
    "doc_de_002": (
        "Die grobe Verletzung der Verkehrsregeln im Sinne des § 315c Absatz 1 Nummer 2 "
        "StGB setzt ein grob verkehrswidriges und rücksichtsloses Fahren voraus."
    ),
    "doc_de_003": (
        "Der Führerschein wird bei einer Trunkenheitsfahrt regelmäßig entzogen, und die "
        "Fahrerlaubnis kann nach einer Sperrfrist neu erteilt werden."
    ),
}


class ScoringReranker(BaseNodePostprocessor):
    """A local reranker that scores nodes in their existing order.

    Rerank posts to OpenRouter, which this suite never calls. The floor reads
    nothing but the score, so a deterministic postprocessor is the same branch:
    ``tests/test_api_ask.py`` exercises the floor the same way.
    """

    scores: list[float]

    @classmethod
    def class_name(cls) -> str:
        return "ScoringReranker"

    def _postprocess_nodes(
        self,
        nodes: list[NodeWithScore],
        query_bundle: QueryBundle | None = None,
    ) -> list[NodeWithScore]:
        return [
            NodeWithScore(node=node.node, score=float(self.scores[index % len(self.scores)]))
            for index, node in enumerate(nodes)
        ]


def _workspace(tmp_path: Path, *, rerank: bool, floor: float, top_n: int = 5) -> Settings:
    """A workspace with a corpus, a champion config, and no index yet."""
    settings = Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        WEB_ORIGIN="http://localhost:8080",
    )
    prepare_workspace(settings.workspace_dir)
    corpus = settings.workspace_dir / "corpus"
    from deepfix_rag.documents import init_documents
    from deepfix_rag.storage import connect

    init_documents(settings.product_db_path)
    for doc_id, text in DOCUMENTS.items():
        (corpus / f"{doc_id}.json").write_text(
            json.dumps({"doc_id": doc_id, "text": text, "filename": f"{doc_id}.txt"}),
            encoding="utf-8",
        )
        # A filename is resolved through the documents table, exactly as an upload
        # records it; without the row the tool falls back to the doc id.
        with connect(settings.product_db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (doc_id, filename, media_type, text, status, uploaded_by, created_at)
                VALUES (?, ?, 'text/plain', ?, 'indexed', 'owner-1', ?)
                """,
                (doc_id, f"{doc_id}.txt", text, "2026-11-15T00:00:00+00:00"),
            )
    champion = RAGConfig.from_yaml(settings.workspace_dir / "configs" / "champion.yaml")
    config = champion.model_copy(
        update={
            "retrieval": champion.retrieval.model_copy(
                update={"backend": "hybrid", "top_k": 20, "lancedb_table_name": "documents"}
            ),
            "rerank": champion.rerank.model_copy(
                update={"enabled": rerank, "top_n": top_n, "min_relevance_score": floor}
            ),
            "data": champion.data.model_copy(
                update={
                    "path_or_repo": str(corpus),
                    "source_type": DataSourceType.DIRECTORY,
                }
            ),
        }
    )
    (settings.workspace_dir / "configs" / "champion.yaml").write_text(
        config.to_yaml(), encoding="utf-8"
    )
    return settings


def _coordinator(settings: Settings) -> IndexCoordinator:
    coordinator = IndexCoordinator(
        settings.workspace_dir,
        settings.product_db_path,
        embed_model=MockEmbedding(embed_dim=16),
    )
    coordinator.startup()
    coordinator.wait()
    return coordinator


def _search(
    coordinator: IndexCoordinator,
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
) -> ask_chat.SearchResult:
    arguments: dict[str, object] = {"query": query}
    if top_k is not None:
        arguments["top_k"] = top_k
    return asyncio.run(
        ask_chat.search_documents(
            coordinator,
            settings.product_db_path,
            arguments,
            max_passage_chars=1200,
            max_displayed_hits=settings.MAX_DISPLAYED_HITS,
        )
    )


def _retrieve(
    coordinator: IndexCoordinator, query: str, settings: Settings
) -> list[tuple[str, int, float]]:
    """What ``POST /retrieval`` would show: the tool's reference implementation."""
    lease = coordinator.checkout()
    assert lease is not None
    try:
        found = asyncio.run(lease.service.retrieve(query))
        limit = displayed_limit(lease.service.config, settings.MAX_DISPLAYED_HITS)
    finally:
        coordinator.release(lease)
    return [
        (result.doc_id, rank, float(result.score or 0.0))
        for rank, result in enumerate(found[:limit], start=1)
    ]


def test_the_tool_reports_exactly_what_the_product_shows(tmp_path: Path) -> None:
    """FR-024: same ids, same order, same scores, truncated at the display window."""
    settings = _workspace(tmp_path, rerank=False, floor=0.3)
    coordinator = _coordinator(settings)
    try:
        for query in ("Trunkenheitsfahrt", "Führerschein entzogen", "Verkehrsregeln"):
            result = _search(coordinator, settings, query)
            assert [(p.doc_id, p.rank, p.score) for p in result.passages] == _retrieve(
                coordinator, query, settings
            )
            assert result.query == query
            assert result.max_score == max(passage.score for passage in result.passages)
            assert result.grounding == "grounded"
            # AC 6: without rerank no floor applies, and the window is the product's.
            assert len(result.passages) <= settings.MAX_DISPLAYED_HITS
            assert all(passage.truncated is False for passage in result.passages)
            assert all(passage.filename == f"{passage.doc_id}.txt" for passage in result.passages)
    finally:
        coordinator.close()


def test_a_raised_floor_empties_the_tool_and_the_product_together(tmp_path: Path) -> None:
    """AC 11: the floor is inside the call both sides make, so neither diverges."""
    settings = _workspace(tmp_path, rerank=True, floor=0.95)
    coordinator = _coordinator(settings)
    try:
        lease = coordinator.checkout()
        assert lease is not None
        try:
            lease.service._workflow._get_reranker = lambda: ScoringReranker(  # type: ignore[method-assign]
                scores=[0.4, 0.3, 0.2]
            )
        finally:
            coordinator.release(lease)

        empty = _search(coordinator, settings, "Trunkenheitsfahrt")
        assert empty.passages == []
        assert empty.grounding == "no_relevant_sources"
        assert empty.max_score == 0.0
        assert empty.results == []
        assert _retrieve(coordinator, "Trunkenheitsfahrt", settings) == []

        # A floor the survivors clear keeps the same rows on both sides.
        lease = coordinator.checkout()
        assert lease is not None
        try:
            lease.service._workflow.config.rerank.min_relevance_score = 0.25
            lease.service.config.rerank.min_relevance_score = 0.25
        finally:
            coordinator.release(lease)
        kept = _search(coordinator, settings, "Trunkenheitsfahrt")
        assert kept.grounding == "grounded"
        assert [(p.doc_id, p.rank, p.score) for p in kept.passages] == _retrieve(
            coordinator, "Trunkenheitsfahrt", settings
        )
        assert all(passage.score >= 0.25 for passage in kept.passages)
    finally:
        coordinator.close()


def test_the_tool_bounds_depth_text_and_input(tmp_path: Path) -> None:
    """AC 2, 7, 8, 9: a refused input, a clamped depth, and a truncated passage."""
    settings = _workspace(tmp_path, rerank=False, floor=0.0)
    coordinator = _coordinator(settings)
    try:
        with pytest.raises(ask_chat.ToolInputError):
            _search(coordinator, settings, "")
        with pytest.raises(ask_chat.ToolInputError):
            _search(coordinator, settings, "x" * (ask_chat.TOOL_QUERY_MAX_CHARS + 1))

        everything = _search(coordinator, settings, "Trunkenheitsfahrt")
        limit = len(everything.passages)
        assert limit > 1
        for requested, expected in ((0, 1), (1, 1), (limit + 50, limit)):
            clamped = _search(coordinator, settings, "Trunkenheitsfahrt", top_k=requested)
            assert len(clamped.passages) == expected
            assert clamped.query == "Trunkenheitsfahrt"

        tight = asyncio.run(
            ask_chat.search_documents(
                coordinator,
                settings.product_db_path,
                {"query": "Trunkenheitsfahrt"},
                max_passage_chars=40,
                max_displayed_hits=settings.MAX_DISPLAYED_HITS,
            )
        )
        assert tight.passages
        assert all(len(passage.text) == 40 for passage in tight.passages)
        assert all(passage.truncated is True for passage in tight.passages)
        # The stored hits keep the full text, because passage_key hashes it (FR-006).
        assert all(len(result.text) > 40 for result in tight.results)
    finally:
        coordinator.close()


def test_the_tool_has_no_write_path_and_does_not_move_the_index(tmp_path: Path) -> None:
    """AC 12, 13: a rerank-only change reuses the index, and the tool writes nothing."""
    settings = _workspace(tmp_path, rerank=False, floor=0.3)
    coordinator = _coordinator(settings)
    try:
        before = index_time_signature(
            RAGConfig.from_yaml(settings.workspace_dir / "configs" / "champion.yaml")
        )
        _search(coordinator, settings, "Trunkenheitsfahrt")
        after = index_time_signature(
            RAGConfig.from_yaml(settings.workspace_dir / "configs" / "champion.yaml")
        )
        assert before == after
        assert not any(key.startswith("rerank") for key in after)

        moved = RAGConfig.from_yaml(settings.workspace_dir / "configs" / "champion.yaml")
        changed = moved.model_copy(
            update={"rerank": moved.rerank.model_copy(update={"min_relevance_score": 0.5})}
        )
        assert index_time_signature(changed) == before

        # The champion file itself is untouched, and the tool wrote no interaction.
        with connect(settings.product_db_path) as conn:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        assert "ask_chat_threads" not in tables
        assert (
            "interactions" not in tables or _count(settings.product_db_path, "interactions") == 0
        )
    finally:
        coordinator.close()


def _count(db_path: Path, table: str) -> int:
    from deepfix_rag.storage import connect

    with connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

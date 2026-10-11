"""Blue-green workspace index builds and index status."""

import json
import os
from pathlib import Path
import threading

from fastapi.testclient import TestClient
from llama_index.core.embeddings import MockEmbedding
import pytest

from deepfix_rag.accounts import create_admin
from deepfix_rag.api.app import create_app
from deepfix_rag.config.schema import DataLoaderConfig, DataSourceType, RAGConfig, RetrievalBackend
from deepfix_rag.config.settings import Settings
from deepfix_rag.data import DataLoader, LocalFileLoader
from deepfix_rag.documents import prepare_workspace
from deepfix_rag.indexing import IndexCoordinator, bind_locations

pytestmark = [pytest.mark.api, pytest.mark.integration]


class BoomEmbedding(MockEmbedding):
    """Embedding model that fails every text embedding."""

    def _get_text_embedding(self, text: str) -> list[float]:
        raise RuntimeError("embed failed")


class Gate:
    """Block the first embedding call until a test releases it."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


def _gated_embedding(gate: Gate) -> MockEmbedding:
    class GatedEmbedding(MockEmbedding):
        """Pause the first embedding call so a second rebuild can overlap it."""

        def _get_text_embedding(self, text: str) -> list[float]:
            gate.started.set()
            if not gate.release.wait(timeout=10):
                raise TimeoutError("The gated embedding was not released.")
            return self._get_vector()

    return GatedEmbedding(embed_dim=16)


@pytest.fixture
def index_settings(tmp_path: Path) -> Settings:
    return Settings(
        PRODUCT_DATA_DIR=tmp_path / "private",
        WORKSPACE_DIR=tmp_path / "workspace",
        ALLOWED_EMAIL_DOMAINS="example.com",
        WEB_ORIGIN="http://localhost:8080",
    )


def _coordinator(settings: Settings, embed_model: MockEmbedding | None = None) -> IndexCoordinator:
    prepare_workspace(settings.workspace_dir)
    return IndexCoordinator(
        settings.workspace_dir,
        settings.product_db_path,
        embed_model=embed_model or MockEmbedding(embed_dim=16),
    )


def _write_corpus(workspace: Path, doc_id: str, text: str, filename: str) -> None:
    payload = {"doc_id": doc_id, "text": text, "filename": filename}
    path = workspace / "corpus" / f"{doc_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_local_loader_reads_corpus_json_and_jsonl(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ws_a.json").write_text(
        json.dumps({"doc_id": "ws_a", "text": "Alpha text", "filename": "a.txt"}),
        encoding="utf-8",
    )
    (corpus / "ws_b.json").write_text(
        json.dumps({"doc_id": "ws_b", "text": "Beta text", "filename": "b.txt"}),
        encoding="utf-8",
    )
    (corpus / "qa.json").write_text("{}\n", encoding="utf-8")
    loaded = LocalFileLoader().load_corpus(
        DataLoaderConfig(path_or_repo=str(corpus), source_type=DataSourceType.LOCAL_FILE)
    )
    assert {document.id_ for document in loaded} == {"ws_a", "ws_b"}

    inferred = DataLoader(DataLoaderConfig(path_or_repo=str(corpus))).load_corpus()
    assert {document.id_ for document in inferred} == {"ws_a", "ws_b"}

    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text(
        json.dumps({"id": "row-1", "text": "Line one"}) + "\n",
        encoding="utf-8",
    )
    rows = LocalFileLoader().load_corpus(DataLoaderConfig(path_or_repo=str(jsonl)))
    assert [(document.id_, document.text) for document in rows] == [("row-1", "Line one")]


def test_bind_locations_ignores_candidate_paths(index_settings: Settings) -> None:
    prepare_workspace(index_settings.workspace_dir)
    config = RAGConfig()
    config.retrieval.lancedb_index_dir = "s3://deepfix-index/dev"
    config.data.path_or_repo = "HF:wirthual/dip-bundestag"
    build_dir = index_settings.workspace_dir / "indexes" / "builds" / "one"
    bound = bind_locations(config, index_settings.workspace_dir, build_dir)
    assert bound.retrieval.lancedb_index_dir == str(build_dir)
    assert "s3://" not in bound.retrieval.lancedb_index_dir
    assert bound.data.source_type == DataSourceType.DIRECTORY


def test_rebuild_switches_the_pointer_and_keeps_the_old_index_on_failure(
    index_settings: Settings,
) -> None:
    workspace = index_settings.workspace_dir
    _write_corpus(workspace, "ws_a", "The harbor budget was approved.", "a.txt")
    coordinator = _coordinator(index_settings)
    coordinator.startup()
    coordinator.wait()
    ready = coordinator.snapshot()
    assert ready.status == "ready"
    assert ready.serving_build_id
    assert ready.chunk_count > 0
    assert ready.document_count == 0
    pointer = workspace / "indexes" / "current"
    assert pointer.is_symlink()
    assert Path(os.readlink(pointer)).name == ready.serving_build_id
    build_dir = workspace / "indexes" / "builds" / ready.serving_build_id
    assert (build_dir / "deepfix-build.json").is_file()
    assert (
        not (pointer / "deepfix-build.json").is_file() or pointer.resolve() == build_dir.resolve()
    )

    lease = coordinator.checkout()
    assert lease is not None
    first_id = lease.build_id
    coordinator.embed_model = BoomEmbedding(embed_dim=16)
    coordinator.request_rebuild()
    coordinator.wait()
    failed = coordinator.snapshot()
    assert failed.serving_build_id == first_id
    assert failed.status == "ready_with_errors"
    assert failed.error
    assert "embed failed" in failed.error
    assert (workspace / "indexes" / "builds" / first_id).is_dir()
    coordinator.release(lease)
    assert (workspace / "indexes" / "builds" / first_id).is_dir()


def test_failure_does_not_replace_a_checked_out_build_and_success_deletes_the_old_one(
    index_settings: Settings,
) -> None:
    workspace = index_settings.workspace_dir
    _write_corpus(workspace, "ws_a", "The harbor budget was approved.", "a.txt")
    coordinator = _coordinator(index_settings)
    coordinator.request_rebuild()
    coordinator.wait()
    lease = coordinator.checkout()
    assert lease is not None
    old_dir = workspace / "indexes" / "builds" / lease.build_id
    coordinator.request_rebuild()
    coordinator.wait()
    current = coordinator.snapshot()
    assert current.serving_build_id != lease.build_id
    assert old_dir.is_dir()
    answered = __import__("asyncio").run(lease.service.retrieve("harbor budget"))
    assert answered
    coordinator.release(lease)
    assert not old_dir.exists()
    assert (workspace / "indexes" / "builds" / current.serving_build_id).is_dir()


def test_index_time_change_rebuilds_and_a_matching_champion_does_not(
    index_settings: Settings,
) -> None:
    workspace = index_settings.workspace_dir
    _write_corpus(workspace, "ws_a", "The harbor budget was approved.", "a.txt")
    coordinator = _coordinator(index_settings)
    coordinator.request_rebuild()
    coordinator.wait()
    first = coordinator.snapshot().serving_build_id
    champion_path = workspace / "configs" / "champion.yaml"
    config = RAGConfig.from_yaml(champion_path)
    config.chunking.size = config.chunking.size + 64
    champion_path.write_text(config.to_yaml(), encoding="utf-8")
    coordinator.refresh()
    coordinator.wait()
    second = coordinator.snapshot().serving_build_id
    assert second != first
    assert not (workspace / "indexes" / "builds" / first).exists()
    manifest = json.loads(
        (workspace / "indexes" / "builds" / second / "deepfix-build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["index_time"]["chunking"]["size"] == config.chunking.size
    coordinator.refresh()
    coordinator.wait()
    assert coordinator.snapshot().serving_build_id == second
    assert list((workspace / "indexes" / "builds").iterdir()) == [
        workspace / "indexes" / "builds" / second
    ]


def test_graph_backend_is_rejected(index_settings: Settings) -> None:
    workspace = index_settings.workspace_dir
    prepare_workspace(workspace)
    _write_corpus(workspace, "ws_a", "The harbor budget was approved.", "a.txt")
    config = RAGConfig.from_yaml(workspace / "configs" / "champion.yaml")
    config.retrieval.backend = RetrievalBackend.GRAPH
    (workspace / "configs" / "champion.yaml").write_text(config.to_yaml(), encoding="utf-8")
    coordinator = _coordinator(index_settings)
    coordinator.startup()
    coordinator.wait()
    snapshot = coordinator.snapshot()
    assert snapshot.status == "failed"
    assert snapshot.serving_build_id is None
    assert "dense, hybrid, and bm25" in (snapshot.error or "")
    builds = workspace / "indexes" / "builds"
    assert not builds.exists() or not any(builds.iterdir())


def test_index_routes_enforce_role_conflict_and_errors(index_settings: Settings) -> None:
    gate = Gate()
    embed_model = _gated_embedding(gate)
    with TestClient(create_app(index_settings, embed_model=embed_model)) as client:
        assert client.get("/index").status_code == 401
        created = client.post(
            "/auth/register",
            json={"email": "ada@example.com", "password": "correct-horse"},
        )
        assert created.status_code == 201
        signed_member = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "correct-horse"},
        )
        assert signed_member.status_code == 200
        denied = client.post("/admin/index/rebuild")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Only admins can rebuild the index."

        create_admin(index_settings.product_db_path, "admin@example.com", "correct-horse")
        signed_in = client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "correct-horse"},
        )
        assert signed_in.status_code == 200
        missing = client.post("/admin/index/rebuild")
        assert missing.status_code == 400
        assert missing.json()["detail"] == "There are no documents to index."

        uploaded = client.post(
            "/documents",
            files={"file": ("notes.txt", b"The harbor budget was approved.", "text/plain")},
        )
        assert uploaded.status_code == 201
        assert gate.started.wait(timeout=10)
        conflict = client.post("/admin/index/rebuild")
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "A rebuild is already running."
        rebuilding = client.get("/index")
        assert rebuilding.status_code == 200
        assert rebuilding.json()["status"] == "rebuilding"
        assert rebuilding.json()["serving_build_id"] is None
        gate.release.set()
        client.app.state.index.wait()
        ready = client.get("/index").json()
        assert ready["status"] == "ready"
        assert ready["serving_build_id"]
        assert ready["chunk_count"] > 0
        assert ready["document_count"] == 1
        assert ready["pending_document_count"] == 0
        assert ready["config_path"].endswith("champion.yaml")

        failed = client.post(
            "/documents",
            data={
                "reading": json.dumps({"text": "", "pages": [{"page": 1, "reasons": ["scanned"]}]})
            },
            files={"file": ("scan.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        assert failed.status_code == 201
        assert failed.json()["status"] == "failed"
        with_errors = client.get("/index").json()
        assert with_errors["status"] == "ready_with_errors"
        assert with_errors["serving_build_id"] == ready["serving_build_id"]

        config = RAGConfig.from_yaml(index_settings.workspace_dir / "configs" / "champion.yaml")
        config.retrieval.backend = RetrievalBackend.MIXED
        (index_settings.workspace_dir / "configs" / "champion.yaml").write_text(
            config.to_yaml(),
            encoding="utf-8",
        )
        rejected = client.post("/admin/index/rebuild")
        assert rejected.status_code == 400
        assert "dense, hybrid, and bm25" in rejected.json()["detail"]

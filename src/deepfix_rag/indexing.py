"""Blue-green builds of the shared workspace index.

A build is written under ``indexes/builds/<build_id>/``. The serving path
``indexes/current`` becomes a symlink only after that build has loaded. Requests
that already hold a query service keep using the previous build directory.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import uuid

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.schema import Document
from pydantic import ValidationError

from deepfix_rag.accounts import connect
from deepfix_rag.config.schema import DataSourceType, RAGConfig, RetrievalBackend
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.data import DataLoader, _corpus_json_documents
from deepfix_rag.documents import (
    document_source,
    documents_with_status,
    init_documents,
    set_document_status,
)
from deepfix_rag.pipeline import RAGWorkflow
from deepfix_rag.service import QueryService

_ALLOWED_BACKENDS = frozenset(
    {RetrievalBackend.DENSE, RetrievalBackend.HYBRID, RetrievalBackend.BM25}
)
_MANIFEST = "deepfix-build.json"
_STATUS = "status.json"


class RebuildInProgress(Exception):
    """A full rebuild is already running."""

    def __init__(self) -> None:
        super().__init__("A rebuild is already running.")


class UnsupportedIndexBackend(Exception):
    """Workspace builds accept dense, hybrid, and bm25 only."""

    def __init__(self) -> None:
        super().__init__("Workspace indexing supports dense, hybrid, and bm25 retrieval only.")


class IndexEmpty(Exception):
    """The corpus has no documents to build."""

    def __init__(self) -> None:
        super().__init__("There are no documents to index.")


class ChampionConfigError(Exception):
    """The champion file cannot be used for a workspace build."""

    def __init__(self) -> None:
        super().__init__("The champion config could not be read.")


@dataclass(frozen=True)
class IndexLease:
    """A query service pinned to one build for the length of a request."""

    build_id: str
    service: QueryService


@dataclass(frozen=True)
class IndexSnapshot:
    """Point-in-time view of the shared index."""

    status: str
    document_count: int
    chunk_count: int
    pending_document_count: int
    updated_at: str | None
    config_path: str
    serving_build_id: str | None
    error: str | None


def index_time_signature(config: RAGConfig) -> dict[str, object]:
    """Return the config fields that require a new index."""
    return {
        "chunking": config.chunking.model_dump(mode="json"),
        "backend": config.retrieval.backend.value,
        "vector_store": config.retrieval.vector_store.value,
        "bit_width": config.retrieval.bit_width,
    }


def bind_locations(config: RAGConfig, workspace: Path, index_dir: Path) -> RAGConfig:
    """Point a config at this workspace, ignoring any carried location fields."""
    return config.model_copy(
        update={
            "retrieval": config.retrieval.model_copy(update={"lancedb_index_dir": str(index_dir)}),
            "data": config.data.model_copy(
                update={
                    "path_or_repo": str(workspace / "corpus"),
                    "source_type": DataSourceType.DIRECTORY,
                }
            ),
        }
    )


class IndexCoordinator:
    """Own builds, the serving pointer, and the in-memory query service."""

    def __init__(
        self,
        workspace: Path,
        db_path: Path,
        *,
        embed_model: BaseEmbedding | None = None,
        llm: BaseLLM | None = None,
    ) -> None:
        self.workspace = workspace
        self.db_path = db_path
        self.embed_model = embed_model
        self.llm = llm
        self._lock = threading.Lock()
        self._rebuilding = False
        self._rebuild_queued = False
        self._jobs: queue.Queue[tuple[str, str]] = queue.Queue()
        self._writer = threading.Thread(target=self._writer_loop, name="index-writer", daemon=True)
        self._writer.start()
        self._service: QueryService | None = None
        self._build_id: str | None = None
        self._refs: dict[str, int] = {}
        self._updated_at: str | None = None
        self._error: str | None = None

    def startup(self) -> None:
        """Load a serving index, then rebuild when it is missing or stale."""
        self._load_persisted()
        try:
            self.refresh()
        except Exception:
            LOGGER.exception("Index startup failed")
        self._requeue_interrupted()

    def close(self) -> None:
        """Finish the current index job, then stop the writer."""
        self._jobs.put(("stop", ""))
        self._writer.join(timeout=120)

    def refresh(self) -> None:
        """Reload the champion and rebuild when the serving index cannot answer."""
        try:
            config = self._read_champion()
        except ChampionConfigError as exc:
            self._set_error(str(exc))
            return
        if config.retrieval.backend not in _ALLOWED_BACKENDS:
            self._set_error(str(UnsupportedIndexBackend()))
            self._load_serving_index()
            return
        pointer = self._pointer_build_id()
        if pointer is None:
            if self._indexable_documents(config):
                self._start_or_skip()
            return
        if self._service is None or self._build_id != pointer:
            try:
                self._publish(pointer)
            except Exception as exc:
                LOGGER.exception("Serving index could not be loaded")
                self._set_error(str(exc))
                return
        if self._service is None:
            return
        manifest = self._read_manifest(pointer)
        if manifest is None or manifest.get("index_time") != index_time_signature(config):
            self._start_or_skip()

    def request_rebuild(self) -> IndexSnapshot:
        """Start a repair rebuild. Raises when the build cannot start."""
        config = self._read_champion()
        if config.retrieval.backend not in _ALLOWED_BACKENDS:
            raise UnsupportedIndexBackend
        if not self._indexable_documents(config):
            raise IndexEmpty
        self.start_rebuild()
        return self.snapshot()

    def on_document_added(self, doc_id: str) -> None:
        """Index one new document, or rebuild when nothing is serving yet."""
        with self._lock:
            served = self._service is not None
        if not served:
            self.schedule_if_unserved()
        self._jobs.put(("upsert", doc_id))

    def on_document_removed(self, doc_id: str) -> None:
        """Queue removal of one document's chunks. Search keeps reading meanwhile."""
        self._jobs.put(("delete", doc_id))

    def schedule_if_unserved(self) -> None:
        """Start the first build after a document arrives and no index is serving."""
        try:
            with self._lock:
                if self._rebuilding or self._service is not None:
                    return
            config = self._read_champion()
            if config.retrieval.backend not in _ALLOWED_BACKENDS:
                self._set_error(str(UnsupportedIndexBackend()))
                return
            if not self._indexable_documents(config):
                return
            self.start_rebuild()
        except RebuildInProgress:
            return
        except ChampionConfigError as exc:
            self._set_error(str(exc))
        except Exception:
            LOGGER.exception("Could not schedule an index rebuild")

    def start_rebuild(self) -> None:
        """Queue one full rebuild, or raise when one is already running or queued."""
        with self._lock:
            if self._rebuilding or self._rebuild_queued:
                raise RebuildInProgress
            self._rebuild_queued = True
        self._jobs.put(("rebuild", ""))

    def wait(self, timeout: float = 120) -> None:
        """Block until every queued index job has finished."""
        if timeout < 0:
            raise TimeoutError("The rebuild did not finish.")
        deadline = time.monotonic() + timeout
        with self._jobs.all_tasks_done:
            while self._jobs.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("The rebuild did not finish.")
                self._jobs.all_tasks_done.wait(remaining)

    def checkout(self) -> IndexLease | None:
        """Pin the serving query service so a later switch keeps its build directory."""
        with self._lock:
            if self._service is None or self._build_id is None:
                return None
            self._refs[self._build_id] = self._refs.get(self._build_id, 0) + 1
            return IndexLease(build_id=self._build_id, service=self._service)

    def release(self, lease: IndexLease) -> None:
        """Drop a pin and delete the build once nothing serves or reads it."""
        with self._lock:
            remaining = self._refs.get(lease.build_id, 0) - 1
            if remaining <= 0:
                self._refs.pop(lease.build_id, None)
            else:
                self._refs[lease.build_id] = remaining
            unused = remaining <= 0 and lease.build_id != self._build_id
        if unused:
            self._delete_build(lease.build_id)

    def snapshot(self) -> IndexSnapshot:
        """Return the status shown by the Library and by Search and Ask."""
        with self._lock:
            rebuilding = self._rebuilding or self._rebuild_queued
            service = self._service
            build_id = self._build_id if service is not None else None
            error = self._error
            updated_at = self._updated_at
        counts = self._document_counts()
        if rebuilding:
            status = "rebuilding"
        elif counts["updating"] > 0 or (service is not None and counts["pending"] > 0):
            status = "updating"
        elif service is None and error:
            status = "failed"
        elif service is None:
            status = "empty"
        elif counts["failed"] > 0 or error:
            status = "ready_with_errors"
        else:
            status = "ready"
        return IndexSnapshot(
            status=status,
            document_count=counts["documents"],
            chunk_count=self._chunk_count(build_id),
            pending_document_count=counts["pending"],
            updated_at=updated_at,
            config_path=str(self._champion_path()),
            serving_build_id=build_id,
            error=error,
        )

    def _start_or_skip(self) -> None:
        try:
            self.start_rebuild()
        except RebuildInProgress:
            return

    def _writer_loop(self) -> None:
        while True:
            kind, doc_id = self._jobs.get()
            try:
                if kind == "stop":
                    return
                if kind == "rebuild":
                    self._run_rebuild()
                elif kind == "upsert":
                    self._upsert_document(doc_id)
                elif kind == "delete":
                    self._delete_document(doc_id)
            except Exception:
                LOGGER.exception("Index writer failed a %s job", kind)
            finally:
                self._jobs.task_done()

    def _requeue_interrupted(self) -> None:
        for doc_id in documents_with_status(self.db_path, ("pending", "indexing")):
            self._jobs.put(("upsert", doc_id))
        for doc_id in documents_with_status(self.db_path, ("deleting",)):
            self._jobs.put(("delete", doc_id))

    def _upsert_document(self, doc_id: str) -> None:
        source = document_source(self.db_path, doc_id)
        if source is None or self._service is None:
            return
        text, filename = source
        set_document_status(self.db_path, doc_id, "indexing")
        try:
            self._service.replace_document(
                Document(text=text, id_=doc_id, metadata={"doc_id": doc_id, "filename": filename})
            )
        except Exception as exc:
            LOGGER.exception("Could not index %s", doc_id)
            set_document_status(
                self.db_path,
                doc_id,
                "failed",
                index_error=str(exc).strip() or "The document could not be indexed.",
            )
            return
        set_document_status(self.db_path, doc_id, "indexed", indexed=True)

    def _delete_document(self, doc_id: str) -> None:
        try:
            if self._service is not None:
                self._service.remove_document(doc_id)
            (self.workspace / "corpus" / f"{doc_id}.json").unlink(missing_ok=True)
        except Exception as exc:
            LOGGER.exception("Could not delete %s from the index", doc_id)
            set_document_status(
                self.db_path,
                doc_id,
                "deleting",
                index_error=str(exc).strip()
                or "The document could not be removed from the index.",
            )
            return
        set_document_status(self.db_path, doc_id, "deleting", deleted=True)

    def _run_rebuild(self) -> None:
        with self._lock:
            self._rebuilding = True
            self._rebuild_queued = False
        build_id = uuid.uuid4().hex
        build_dir = self._build_path(build_id)
        try:
            config = bind_locations(self._read_champion(), self.workspace, build_dir)
            if config.retrieval.backend not in _ALLOWED_BACKENDS:
                raise UnsupportedIndexBackend
            build_dir.mkdir(parents=True, exist_ok=True)
            documents = self._indexable_documents(config)
            if not documents:
                raise IndexEmpty
            workflow = RAGWorkflow(config=config, embed_model=self.embed_model, llm=self.llm)
            index = workflow._build_index(documents)
            chunk_count = workflow._count_index_chunks(index)
            manifest = {
                "build_id": build_id,
                "index_time": index_time_signature(config),
                "chunk_count": chunk_count,
                "document_ids": [str(document.id_) for document in documents],
                "created_at": _now(),
            }
            (build_dir / _MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            self._publish(build_id)
            self._switch_pointer(build_id)
            self._mark_indexed(manifest["document_ids"])
            self._set_error(None)
            self._updated_at = _now()
            self._persist()
        except Exception as exc:
            LOGGER.exception("Index rebuild failed")
            message = str(exc).strip() or "The index rebuild failed."
            self._set_error(message)
            if self._build_id != build_id:
                self._delete_build(build_id)
        finally:
            with self._lock:
                self._rebuilding = False

    def _publish(self, build_id: str) -> None:
        build_dir = self._build_path(build_id)
        config = bind_locations(self._read_champion(), self.workspace, build_dir)
        service = QueryService(config, build_dir, embed_model=self.embed_model, llm=self.llm)
        with self._lock:
            previous = self._build_id
            self._service = service
            self._build_id = build_id
            unused = (
                previous is not None and previous != build_id and self._refs.get(previous, 0) <= 0
            )
        if unused and previous is not None:
            self._delete_build(previous)

    def _load_serving_index(self) -> None:
        pointer = self._pointer_build_id()
        if pointer is None or self._service is not None:
            return
        try:
            self._publish(pointer)
        except Exception as exc:
            LOGGER.exception("Serving index could not be loaded")
            self._set_error(str(exc))

    def _switch_pointer(self, build_id: str) -> None:
        indexes = self.workspace / "indexes"
        pointer = indexes / "current"
        temporary = indexes / f".next-{build_id}"
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        temporary.symlink_to(Path("builds") / build_id, target_is_directory=True)
        if pointer.is_symlink():
            os.replace(temporary, pointer)
            return
        if pointer.exists():
            if pointer.is_dir() and any(pointer.iterdir()):
                temporary.unlink()
                raise RuntimeError(
                    "The serving path is a directory, so the build was not switched."
                )
            if pointer.is_dir():
                pointer.rmdir()
            else:
                pointer.unlink()
        os.replace(temporary, pointer)

    def _read_champion(self) -> RAGConfig:
        path = self._champion_path()
        try:
            return RAGConfig.from_yaml(path)
        except (OSError, ValidationError, ValueError) as exc:
            raise ChampionConfigError from exc

    def _champion_path(self) -> Path:
        return self.workspace / "configs" / "champion.yaml"

    def _build_path(self, build_id: str) -> Path:
        return self.workspace / "indexes" / "builds" / build_id

    def _pointer_build_id(self) -> str | None:
        pointer = self.workspace / "indexes" / "current"
        if not pointer.is_symlink():
            return None
        return Path(os.readlink(pointer)).name

    def _read_manifest(self, build_id: str) -> dict[str, object] | None:
        path = self._build_path(build_id) / _MANIFEST
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        return loaded

    def _chunk_count(self, build_id: str | None) -> int:
        if build_id is None:
            return 0
        with self._lock:
            service = self._service if self._build_id == build_id else None
        if service is not None:
            try:
                return service.chunk_count()
            except Exception:
                LOGGER.debug("Could not count live chunks", exc_info=True)
        manifest = self._read_manifest(build_id)
        if manifest is None:
            return 0
        count = manifest.get("chunk_count", 0)
        return count if isinstance(count, int) else 0

    def _indexable_documents(self, config: RAGConfig):
        corpus = self.workspace / "corpus"
        if _corpus_json_documents(corpus) is None:
            return []
        bound = bind_locations(config, self.workspace, self.workspace / "indexes" / "current")
        deleted = self._deleted_ids()
        return [
            document
            for document in DataLoader(bound.data).load_corpus()
            if str(document.id_) not in deleted
        ]

    def _deleted_ids(self) -> set[str]:
        init_documents(self.db_path)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT doc_id FROM documents WHERE deleted_at IS NOT NULL"
            ).fetchall()
        return {row["doc_id"] for row in rows}

    def _document_counts(self) -> dict[str, int]:
        init_documents(self.db_path)
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END), 0) AS documents,
                    COALESCE(SUM(
                        CASE
                            WHEN deleted_at IS NULL AND status IN ('pending', 'indexing') THEN 1
                            ELSE 0
                        END
                    ), 0) AS pending,
                    COALESCE(SUM(
                        CASE WHEN deleted_at IS NULL AND status = 'failed' THEN 1 ELSE 0 END
                    ), 0) AS failed,
                    COALESCE(SUM(
                        CASE
                            WHEN deleted_at IS NULL AND status IN ('indexing', 'deleting') THEN 1
                            ELSE 0
                        END
                    ), 0) AS updating
                FROM documents
                """
            ).fetchone()
        return {
            "documents": int(row["documents"]),
            "pending": int(row["pending"]),
            "failed": int(row["failed"]),
            "updating": int(row["updating"]),
        }

    def _mark_indexed(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            return
        moment = _now()
        init_documents(self.db_path)
        with connect(self.db_path) as conn:
            conn.executemany(
                """
                UPDATE documents
                SET status = 'indexed', indexed_at = ?, index_error = NULL
                WHERE doc_id = ?
                  AND deleted_at IS NULL
                  AND status NOT IN ('failed', 'deleting')
                """,
                [(moment, doc_id) for doc_id in doc_ids],
            )

    def _delete_build(self, build_id: str) -> None:
        path = self._build_path(build_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def _set_error(self, message: str | None) -> None:
        self._error = message
        self._updated_at = _now()
        self._persist()

    def _persist(self) -> None:
        path = self.workspace / "indexes" / _STATUS
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated_at": self._updated_at, "error": self._error}
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _load_persisted(self) -> None:
        path = self.workspace / "indexes" / _STATUS
        if not path.is_file():
            return
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(loaded, dict):
            return
        updated = loaded.get("updated_at")
        error = loaded.get("error")
        self._updated_at = updated if isinstance(updated, str) else None
        self._error = error if isinstance(error, str) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

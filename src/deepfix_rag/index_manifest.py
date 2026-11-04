"""Index-time signature and the manifest `deepfix index` writes for `eval`."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import settings

MANIFEST_NAME = "deepfix_index.json"
SCHEMA_VERSION = "1.1"


class StaleIndexError(Exception):
    """The index on disk was not built from the config about to be scored."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def corpus_content_sha256(path_or_repo: str) -> str:
    """Hash local corpus bytes, or the identifier when the corpus is not on disk."""
    path = Path(path_or_repo)
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    if path.is_dir():
        files = sorted(item for item in path.rglob("*") if item.is_file())
        for file in files:
            digest.update(file.relative_to(path).as_posix().encode())
            digest.update(b"\0")
            digest.update(file.read_bytes())
        return digest.hexdigest()
    digest.update(path_or_repo.encode())
    return digest.hexdigest()


def index_time_signature(config: RAGConfig) -> dict[str, object]:
    """Return the inputs that require a new index.

    Query-time knobs (top_k, fusion, rerank, language) are omitted. The embedding
    model comes from the process environment, and the corpus hash covers the
    documents the config points at.
    """
    return {
        "chunking": config.chunking.model_dump(mode="json"),
        "backend": config.retrieval.backend.value,
        "vector_store": config.retrieval.vector_store.value,
        "bit_width": config.retrieval.bit_width,
        "embedding_model": settings.EMBEDDING_MODEL,
        "corpus_sha256": corpus_content_sha256(config.data.path_or_repo),
        "graph_store": config.retrieval.graph_store.value,
        "graph_extraction_schema": config.retrieval.graph_extraction_schema,
        "graph_max_paths_per_chunk": config.retrieval.graph_max_paths_per_chunk,
    }


def signature_hash(signature: dict[str, object]) -> str:
    """Stable hash of an index-time signature."""
    payload = json.dumps(signature, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def index_time_key_names() -> list[str]:
    """Dotted names skills should cite instead of copying the signature by hand."""
    return [
        "chunking.*",
        "retrieval.backend",
        "retrieval.vector_store",
        "retrieval.bit_width",
        "EMBEDDING_MODEL",
        "corpus_sha256",
        "retrieval.graph_store",
        "retrieval.graph_extraction_schema",
        "retrieval.graph_max_paths_per_chunk",
    ]


def _manifest_path(index_dir: str | Path) -> Path:
    return Path(index_dir) / MANIFEST_NAME


def _remote(index_dir: str | Path) -> bool:
    return str(index_dir).startswith("s3://")


def build_manifest(config: RAGConfig) -> dict[str, Any]:
    """Manifest body for one finished build."""
    signature = index_time_signature(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "signature": signature,
        "signature_hash": signature_hash(signature),
        "embedding_model": settings.EMBEDDING_MODEL,
        "corpus_sha256": signature["corpus_sha256"],
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def write_index_manifest(index_dir: str | Path, config: RAGConfig) -> dict[str, Any] | None:
    """Write `<index_dir>/deepfix_index.json`. Remote URIs are left untouched."""
    if _remote(index_dir):
        return None
    path = Path(index_dir)
    path.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(config)
    _manifest_path(path).write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return payload


def read_index_manifest(index_dir: str | Path) -> dict[str, Any] | None:
    """Return the manifest, or None when this directory has none."""
    if _remote(index_dir):
        return None
    path = _manifest_path(index_dir)
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise StaleIndexError(f"Index manifest at {path} is not a JSON object.")
    return loaded


def manifest_matches(index_dir: str | Path, config: RAGConfig) -> bool:
    """True when the on-disk manifest was built from this config's index-time inputs."""
    manifest = read_index_manifest(index_dir)
    if manifest is None:
        return False
    return manifest.get("signature") == index_time_signature(config)


def assert_index_current(index_dir: str | Path | None, config: RAGConfig) -> None:
    """Raise StaleIndexError when a local index does not match the config.

    A missing directory is not stale: eval may build it. An s3:// URI is not
    checked, because the CLI cannot read a manifest there. A non-empty local
    directory without a manifest is stale.
    """
    if index_dir is None or _remote(index_dir):
        return
    path = Path(index_dir)
    if not path.exists():
        return
    if not path.is_dir():
        raise StaleIndexError(f"Index path '{index_dir}' is not a directory.")
    manifest = read_index_manifest(path)
    if manifest is None:
        if any(path.iterdir()):
            raise StaleIndexError(
                f"Index directory '{index_dir}' has no {MANIFEST_NAME}. "
                "Rebuild it with `deepfix index` before scoring."
            )
        return
    expected = index_time_signature(config)
    if manifest.get("signature") != expected:
        raise StaleIndexError(
            f"Index at '{index_dir}' does not match this config "
            f"(manifest {manifest.get('signature_hash')}, "
            f"config {signature_hash(expected)}). Rebuild with `deepfix index`."
        )

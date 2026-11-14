"""Local copies of a remote index, for a run that must not re-read object storage.

Two consumers need the same thing: ``deepfix hpo`` localizes the champion index
once so its trials do not fetch it per trial, and ``deepfix eval`` reads that
copy so a baseline/confirmation comparison stays local. Both need the copy to be
a working index directory, which means the manifest the staleness check reads
(``deepfix_index.json``) has to be there too.

A remote ``s3://`` index is addressed as ``bucket/key``. pyarrow reads a bare key
as ``bucket/key`` with the first segment as the bucket, so every selector base
and every stripped prefix here is bucket-qualified.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.index_manifest import (
    MANIFEST_NAME,
    build_manifest,
    read_index_manifest,
)

_REMOTE_SCHEMES = ("s3://", "gs://", "az://", "abfs://", "abfss://", "hdfs://", "gcs://")


def is_remote_index_uri(uri: str) -> bool:
    """True for an index outside the local filesystem."""
    return str(uri).strip().lower().startswith(_REMOTE_SCHEMES)


def index_dir_populated(path: str | Path) -> bool:
    """True when the directory exists and holds anything at all."""
    try:
        candidate = Path(path)
        return candidate.is_dir() and any(candidate.iterdir())
    except OSError:
        return False


def split_s3_uri(uri: str) -> tuple[str, str]:
    """Bucket and key prefix for an ``s3://bucket/key`` URI.

    The bucket is *not* part of the key. Passing a bare key to pyarrow makes it
    treat the first path segment as the bucket, which is how
    ``s3://deepfix-index/optimization/runs/x/index`` turned into a lookup for a
    bucket named ``optimization``.
    """
    remainder = str(uri)[len("s3://") :]
    bucket, _, key = remainder.partition("/")
    if not bucket:
        raise ValueError(f"S3 URI has no bucket: {uri}")
    return bucket, key.strip("/")


def s3_object_prefix(uri: str) -> str:
    """The ``bucket/key`` prefix pyarrow addresses objects by."""
    bucket, key = split_s3_uri(uri)
    if not key:
        return bucket
    return f"{bucket}/{key}"


def relative_key(path: str, object_prefix: str) -> str:
    """The part of ``path`` below ``object_prefix``, with no leading separator."""
    trimmed = str(path).lstrip("/")
    prefix = object_prefix.strip("/")
    if not prefix:
        return trimmed
    if trimmed == prefix:
        return ""
    if trimmed.startswith(prefix + "/"):
        return trimmed[len(prefix) + 1 :]
    return trimmed


def s3_filesystem() -> Any:
    """pyarrow S3 filesystem for the RustFS endpoint the rest of the CLI uses."""
    from pyarrow import fs

    endpoint = str(settings.S3_ENDPOINT_URL or "").strip()
    return fs.S3FileSystem(
        endpoint_override=endpoint or None,
        access_key=settings.S3_ACCESS_KEY_ID or None,
        secret_key=settings.S3_SECRET_ACCESS_KEY or None,
        region=settings.S3_REGION or None,
        scheme="http" if endpoint.startswith("http://") else "https",
    )


def copy_index_tree(
    source: str,
    target: Path,
    *,
    filesystem: Any = None,
) -> Path:
    """Copy a directory or a remote object prefix into ``target``.

    A local directory is copied with ``copytree``. A remote ``s3://bucket/key``
    prefix is walked with pyarrow's S3 filesystem, which reads the same
    ``S3_*`` settings LanceDB already uses. The bucket stays the bucket and the
    key stays the key, so the copy lands the index's own files under
    ``target``. No full-object download is repeated once the files are on disk.
    """
    target.mkdir(parents=True, exist_ok=True)
    if not is_remote_index_uri(source):
        source_path = Path(source)
        if not source_path.is_dir():
            raise FileNotFoundError(f"Index directory does not exist: {source}")
        shutil.copytree(source_path, target, dirs_exist_ok=True)
        return target
    if not str(source).lower().startswith("s3://"):
        raise ValueError(f"Only s3:// index URIs can be copied: {source}")
    _copy_object_prefix(source, target, filesystem=filesystem)
    return target


def _copy_object_prefix(uri: str, target: Path, *, filesystem: Any = None) -> None:
    from pyarrow import fs

    object_prefix = s3_object_prefix(uri)
    s3 = filesystem if filesystem is not None else s3_filesystem()
    selector = fs.FileSelector(object_prefix, recursive=True)
    infos = s3.get_file_info(selector)
    copied = 0
    for info in infos:
        if info.type != fs.FileType.File:
            continue
        relative = relative_key(info.path, object_prefix)
        if not relative:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with s3.open_input_stream(info.path) as reader:
            destination.write_bytes(reader.read())
        copied += 1
    if copied == 0:
        raise FileNotFoundError(f"No index files found under {uri}")
    LOGGER.info(f"Copied {copied} index files from {uri} into {target}")


def write_localized_manifest(
    target: Path,
    config: RAGConfig,
    *,
    source: str,
) -> dict[str, Any]:
    """Write the manifest a later ``eval`` reads, for a copied index directory.

    The object-store index may carry no manifest (``write_index_manifest`` leaves
    ``s3://`` untouched), and a manifest built on another host can hash a
    different corpus path. Either way a copied cache would fail the staleness
    check, which is what pushed HPO-007 back to reading S3. The manifest here is
    built from the config the copy was localized for, so the next eval of that
    config accepts the cache.

    ``localized_from`` and the previous signature hash are recorded so the
    provenance stays visible; ``assert_index_current`` only compares
    ``signature``.
    """
    target.mkdir(parents=True, exist_ok=True)
    previous = read_index_manifest(target)
    payload = build_manifest(config)
    payload["localized_from"] = source
    payload["localized_at"] = datetime.now(timezone.utc).isoformat()
    if previous is not None:
        payload["replaced_signature_hash"] = previous.get("signature_hash")
    (target / MANIFEST_NAME).write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return payload


def localize_index(
    source: str,
    target: Path,
    config: RAGConfig,
    *,
    filesystem: Any = None,
) -> Path:
    """Copy ``source`` into ``target`` and make ``target`` a scoreable index.

    Used both by HPO (``<run_dir>/idx-cache``) and by ``eval`` when it is asked
    for a cache directory that does not exist yet. The manifest is written last
    so an interrupted copy never looks like a complete index.
    """
    copy_index_tree(source, target, filesystem=filesystem)
    write_localized_manifest(target, config, source=source)
    return target

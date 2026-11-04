"""Stage one optimization run under a fresh RustFS prefix.

``stage_run_bundle`` is what API code runs before ``POST /v1/runs``. The
OpenHands path does not call it. The Hermes client (a later story) does.

The prefix is ``s3://<bucket>/optimization/runs/<run_id>/``. ``<bucket>`` is
the bucket in ``S3_URI``. The root key writes ``input/`` and later reads
``output/``. Hermes uses a different key that can only touch
``optimization/runs/*``.

Holdout rows are not an input. Pass their query texts and ids to
``stage_run_bundle`` and the publish is refused if any object would contain
them. Promotion does not read ``candidate-index``. ``apply_candidate``
rebuilds a local index with ``IndexCoordinator.build_beside``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Protocol
import urllib.parse

import boto3
from botocore.config import Config

from deepfix_rag.config.schema import DataSourceType, RAGConfig

CONTAINER_WORK_ROOT = Path("/opt/data/workspace")
RUN_PREFIX = "optimization/runs"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class RunBundleError(ValueError):
    """The run bundle cannot be staged."""


@dataclass(frozen=True)
class BundleObject:
    """One object the root key writes."""

    key: str
    body: bytes


@dataclass(frozen=True)
class RunBundlePlan:
    """Object-key layout for one run, including the bytes to upload."""

    run_id: str
    bucket: str
    prefix: str
    objects: tuple[BundleObject, ...]
    container_work_dir: str
    champion_index_dir: str
    corpus_path: str
    candidate_index_uri: str

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(obj.key for obj in self.objects)


@dataclass(frozen=True)
class StoreRequest:
    """One object-store call. Bodies and credentials are not recorded."""

    operation: str
    bucket: str
    key: str


class ObjectStore(Protocol):
    """The operations the bundle writer and the sync helper share."""

    def put(self, bucket: str, key: str, body: bytes) -> None: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def delete(self, bucket: str, key: str) -> None: ...

    def list(self, bucket: str, prefix: str) -> list[str]: ...


class DirectoryObjectStore:
    """Object keys laid out as files under ``root/bucket/key``.

    Unit tests use this. The suite has no S3 stand-in.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, bucket: str, key: str, body: bytes) -> None:
        path = _object_path(self.root, bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def get(self, bucket: str, key: str) -> bytes:
        path = _object_path(self.root, bucket, key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()

    def delete(self, bucket: str, key: str) -> None:
        path = _object_path(self.root, bucket, key)
        if path.is_file():
            path.unlink()

    def list(self, bucket: str, prefix: str) -> list[str]:
        _validate_bucket(bucket)
        base = (self.root / bucket).resolve()
        if not base.is_dir():
            return []
        keys: list[str] = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(base):
                continue
            rel = resolved.relative_to(base).as_posix()
            if rel.startswith(prefix):
                keys.append(rel)
        return sorted(keys)


class S3ObjectStore:
    """RustFS or any S3 endpoint, using path-style addressing."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
    ) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def put(self, bucket: str, key: str, body: bytes) -> None:
        self._client.put_object(Bucket=bucket, Key=key, Body=body)

    def get(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def delete(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def list(self, bucket: str, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item.get("Key")
                if isinstance(key, str):
                    keys.append(key)
        return keys


class RecordingObjectStore:
    """Forward calls and keep the request plan. Bodies are not stored."""

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner
        self.requests: list[StoreRequest] = []

    def put(self, bucket: str, key: str, body: bytes) -> None:
        self.requests.append(StoreRequest("put", bucket, key))
        self.inner.put(bucket, key, body)

    def get(self, bucket: str, key: str) -> bytes:
        self.requests.append(StoreRequest("get", bucket, key))
        return self.inner.get(bucket, key)

    def delete(self, bucket: str, key: str) -> None:
        self.requests.append(StoreRequest("delete", bucket, key))
        self.inner.delete(bucket, key)

    def list(self, bucket: str, prefix: str) -> list[str]:
        self.requests.append(StoreRequest("list", bucket, prefix))
        return self.inner.list(bucket, prefix)


def validate_run_id(run_id: str) -> str:
    """Reject a run id that could escape ``optimization/runs/<run_id>/``."""
    if run_id in {".", ".."} or _RUN_ID.fullmatch(run_id) is None:
        raise RunBundleError("run_id must be a single path segment")
    return run_id


def bucket_from_s3_uri(uri: str) -> str:
    """Return the bucket in an ``s3://bucket/prefix`` URI."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise RunBundleError("S3_URI must be an s3://bucket URI")
    return _validate_bucket(parsed.netloc)


def run_prefix(run_id: str) -> str:
    """Key prefix for one run, with no leading or trailing slash."""
    return f"{RUN_PREFIX}/{validate_run_id(run_id)}"


def container_work_dir(run_id: str, root: Path = CONTAINER_WORK_ROOT) -> Path:
    """Directory the sync helper downloads ``input/`` into."""
    return root / "runs" / validate_run_id(run_id)


def champion_index_dir(run_id: str, root: Path = CONTAINER_WORK_ROOT) -> str:
    """In-container path of the copied serving index."""
    return str(container_work_dir(run_id, root) / "input" / "champion-index")


def corpus_container_path(run_id: str, root: Path = CONTAINER_WORK_ROOT) -> str:
    """In-container corpus path written into ``data.path_or_repo``."""
    return str(container_work_dir(run_id, root) / "input" / "corpus")


def candidate_index_uri(bucket: str, run_id: str) -> str:
    """``s3://`` URI the agent passes to ``deepfix index --index-dir``.

    ``deepfix index`` accepts that URI and skips the local manifest. The API
    promotion path does not read it.
    """
    _validate_bucket(bucket)
    return f"s3://{bucket}/{run_prefix(run_id)}/candidate-index"


def rewrite_champion_yaml(config: RAGConfig, *, index_dir: str, corpus_path: str) -> str:
    """Point the champion snapshot at the downloaded index and corpus."""
    updated = config.model_copy(
        update={
            "retrieval": config.retrieval.model_copy(update={"lancedb_index_dir": index_dir}),
            "data": config.data.model_copy(
                update={
                    "path_or_repo": corpus_path,
                    "source_type": DataSourceType.DIRECTORY,
                }
            ),
        }
    )
    return updated.to_yaml()


def plan_run_bundle(
    *,
    run_id: str,
    workspace: Path,
    dev_path: Path,
    bucket: str,
    serving_index: Path | None = None,
) -> RunBundlePlan:
    """Read the workspace and return the objects for this run's prefix.

    The plan copies the dev file it is given, the corpus directory, the
    serving index, and ``runs/ledger.jsonl`` when that file exists. It does
    not read a holdout file.
    """
    checked_id = validate_run_id(run_id)
    checked_bucket = _validate_bucket(bucket)
    prefix = run_prefix(checked_id)
    champion_path = workspace / "configs" / "champion.yaml"
    if not champion_path.is_file():
        raise RunBundleError("champion config is missing")
    if not dev_path.is_file():
        raise RunBundleError("dev feedback file is missing")
    corpus_root = (workspace / "corpus").resolve()
    if not corpus_root.is_dir():
        raise RunBundleError("corpus directory is missing")
    index_root = (serving_index or (workspace / "indexes" / "current")).resolve()
    if not index_root.is_dir():
        raise RunBundleError("serving index directory is missing")
    index_files = _files_under(index_root)
    if not index_files:
        raise RunBundleError("serving index is empty")

    index_dir = champion_index_dir(checked_id)
    corpus_path = corpus_container_path(checked_id)
    config = RAGConfig.from_yaml(champion_path)
    rewritten = rewrite_champion_yaml(config, index_dir=index_dir, corpus_path=corpus_path)
    objects = [
        BundleObject(f"{prefix}/input/champion.yaml", rewritten.encode("utf-8")),
        BundleObject(f"{prefix}/input/feedback_dev.jsonl", dev_path.read_bytes()),
    ]
    objects.extend(
        BundleObject(f"{prefix}/input/corpus/{rel}", path.read_bytes())
        for rel, path in _files_under(corpus_root)
    )
    ledger = workspace / "runs" / "ledger.jsonl"
    if ledger.is_file():
        objects.append(BundleObject(f"{prefix}/input/ledger.jsonl", ledger.read_bytes()))
    objects.extend(
        BundleObject(f"{prefix}/input/champion-index/{rel}", path.read_bytes())
        for rel, path in index_files
    )
    work_dir = container_work_dir(checked_id)
    return RunBundlePlan(
        run_id=checked_id,
        bucket=checked_bucket,
        prefix=prefix,
        objects=tuple(objects),
        container_work_dir=str(work_dir),
        champion_index_dir=index_dir,
        corpus_path=corpus_path,
        candidate_index_uri=candidate_index_uri(checked_bucket, checked_id),
    )


def reject_holdout_leaks(
    plan: RunBundlePlan,
    *,
    queries: Sequence[str] = (),
    query_ids: Sequence[str] = (),
) -> None:
    """Refuse the plan when any object key or body contains a holdout needle.

    The error text does not include the query or the id.
    """
    needles = [item.encode("utf-8") for item in (*queries, *query_ids) if item]
    if not needles:
        return
    for obj in plan.objects:
        haystack = obj.key.encode("utf-8") + b"\n" + obj.body
        if any(needle in haystack for needle in needles):
            raise RunBundleError("refusing to upload a bundle that contains holdout content")


def publish_plan(plan: RunBundlePlan, store: ObjectStore) -> tuple[StoreRequest, ...]:
    """Put every planned object. Returns the request plan, without bodies."""
    recorder = RecordingObjectStore(store)
    for obj in plan.objects:
        recorder.put(plan.bucket, obj.key, obj.body)
    return tuple(recorder.requests)


def stage_run_bundle(
    *,
    run_id: str,
    workspace: Path,
    dev_path: Path,
    store: ObjectStore,
    bucket: str,
    serving_index: Path | None = None,
    holdout_queries: Sequence[str] = (),
    holdout_query_ids: Sequence[str] = (),
) -> RunBundlePlan:
    """Write a fresh run prefix with the root key.

    Call this before ``POST /v1/runs``. A second run id gets a different
    prefix, so it never opens the earlier run's LanceDB directory.
    """
    plan = plan_run_bundle(
        run_id=run_id,
        workspace=workspace,
        dev_path=dev_path,
        bucket=bucket,
        serving_index=serving_index,
    )
    reject_holdout_leaks(plan, queries=holdout_queries, query_ids=holdout_query_ids)
    publish_plan(plan, store)
    return plan


def _validate_bucket(bucket: str) -> str:
    if _BUCKET.fullmatch(bucket) is None or ".." in bucket:
        raise RunBundleError("bucket name is not valid")
    return bucket


def _object_path(root: Path, bucket: str, key: str) -> Path:
    _validate_bucket(bucket)
    if not key or key.startswith("/") or "\\" in key or ".." in key.split("/"):
        raise RunBundleError("object key is not valid")
    base = (root / bucket).resolve()
    path = base.joinpath(*key.split("/")).resolve()
    if not path.is_relative_to(base):
        raise RunBundleError("object key escapes the bucket")
    return path


def _files_under(root: Path) -> list[tuple[str, Path]]:
    """Files inside ``root``, following a symlink that ``root`` itself is."""
    if not root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise RunBundleError("a file escapes the directory being copied") from exc
        found.append((relative.as_posix(), resolved))
    return found

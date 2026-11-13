"""Remote index localization: the bucket stays the bucket and the key stays the key.

The failure this covers: ``copy_index_tree`` stripped ``s3://`` and passed the
rest of the URI as a key, so pyarrow read ``optimization`` as a bucket name and
the cache copy failed. These tests use a fake S3 filesystem, so no live
endpoint and no ``unittest.mock`` of domain code is involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.hpo import HyperparameterOpt
from deepfix_rag.index_cache import (
    copy_index_tree,
    index_dir_populated,
    is_remote_index_uri,
    localize_index,
    relative_key,
    s3_object_prefix,
    split_s3_uri,
)
from deepfix_rag.index_manifest import index_time_signature

pytestmark = [pytest.mark.unit]

BUCKET = "deepfix-index"
KEY = "optimization/runs/bt500-20261112/index"
URI = f"s3://{BUCKET}/{KEY}"


class _FakeS3:
    """The slice of pyarrow's S3FileSystem that ``copy_index_tree`` uses.

    pyarrow addresses objects as ``bucket/key``: a selector's ``base_dir`` is a
    key under the default bucket, but every ``FileInfo.path`` it returns and
    every path ``open_input_stream`` takes includes the bucket. This fake does
    the same, which is what makes a copy that forgets the bucket fail here.
    """

    def __init__(self, objects: dict[str, bytes], bucket: str = BUCKET) -> None:
        self.objects = objects
        self.bucket = bucket
        self.selectors: list[tuple[str, bool]] = []
        self.opened: list[str] = []

    def _full(self, key: str) -> str:
        return f"{self.bucket}/{key}"

    def get_file_info(self, selector):  # noqa: ANN001 - pyarrow API
        from pyarrow import fs

        self.selectors.append((selector.base_dir, selector.recursive))
        # pyarrow takes a bucket-qualified base and matches keys under it.
        base = selector.base_dir.rstrip("/")
        if not base.startswith(self.bucket + "/"):
            raise AssertionError(
                f"selector base {base!r} has no bucket prefix; pyarrow would read "
                "the first segment as the bucket name"
            )
        key_base = base[len(self.bucket) + 1 :]
        entries = []
        for key, payload in self.objects.items():
            if key == key_base or key.startswith(key_base + "/"):
                entries.append(
                    SimpleNamespace(path=self._full(key), type=fs.FileType.File, size=len(payload))
                )
        entries.insert(
            0,
            SimpleNamespace(path=self._full(key_base + "/"), type=fs.FileType.Directory, size=0),
        )
        return entries

    def open_input_stream(self, path: str):  # noqa: ANN001 - pyarrow API
        import io

        # pyarrow requires the bucket here and reads a bare key's first segment
        # as the bucket name. Without this check the bug is invisible.
        if not path.startswith(self.bucket + "/"):
            raise AssertionError(
                f"open_input_stream({path!r}) has no bucket prefix; pyarrow would "
                "read the first segment as the bucket name"
            )
        self.opened.append(path)
        return io.BytesIO(self.objects[path[len(self.bucket) + 1 :]])


ORIGIN_MANIFEST = {
    "schema_version": "1.1",
    "signature_hash": "origin-host-signature",
    "signature": {"backend": "hybrid"},
}


def _index_objects() -> dict[str, bytes]:
    """An index as another host left it: a manifest plus the LanceDB payload."""
    return {
        f"{KEY}/deepfix_index.json": json.dumps(ORIGIN_MANIFEST).encode(),
        f"{KEY}/documents.lance/data/0.arrow": b"arrow-bytes",
        f"{KEY}/documents.lance/_versions/1.manifest": b"manifest",
    }


def test_split_s3_uri_keeps_the_bucket_out_of_the_key() -> None:
    assert split_s3_uri(URI) == (BUCKET, KEY)
    assert split_s3_uri(f"s3://{BUCKET}") == (BUCKET, "")
    assert split_s3_uri(f"s3://{BUCKET}/a/b/") == (BUCKET, "a/b")
    with pytest.raises(ValueError, match="no bucket"):
        split_s3_uri("s3:///a/b")


def test_the_object_prefix_includes_the_bucket() -> None:
    assert s3_object_prefix(URI) == f"{BUCKET}/{KEY}"
    assert s3_object_prefix(f"s3://{BUCKET}") == BUCKET


def test_relative_key_strips_only_the_bucket_key_prefix() -> None:
    root = f"{BUCKET}/{KEY}"
    assert relative_key(f"{root}/documents.lance/x.arrow", root) == "documents.lance/x.arrow"
    assert relative_key(f"{root}/deepfix_index.json", root) == "deepfix_index.json"
    assert relative_key(root, root) == ""
    assert relative_key("/a/b/c.arrow", "") == "a/b/c.arrow"
    # A path from a different bucket keeps its own bucket, so nothing is lost.
    assert relative_key("other/x.arrow", root) == "other/x.arrow"


def test_the_copy_reads_the_bucket_and_key_and_writes_index_files(tmp_path: Path) -> None:
    """The regression: `optimization` must not be treated as the bucket."""
    fake = _FakeS3(_index_objects())
    target = tmp_path / "idx-cache"

    copy_index_tree(URI, target, filesystem=fake)

    # pyarrow paths include the bucket, so the selector base must too. Reading
    # `optimization` as a bucket is exactly the bug this covers.
    assert fake.selectors == [(f"{BUCKET}/{KEY}", True)]
    # Every read carries the bucket, which is what the old code dropped.
    assert sorted(fake.opened) == sorted(f"{BUCKET}/{key}" for key in _index_objects())
    assert all(path.startswith(f"{BUCKET}/") for path in fake.opened)

    # Files land relative to the cache root, so the copy is itself an index dir.
    copied_manifest = json.loads((target / "deepfix_index.json").read_text(encoding="utf-8"))
    assert copied_manifest["schema_version"] == "1.1"
    assert (target / "documents.lance" / "data" / "0.arrow").read_bytes() == b"arrow-bytes"
    assert (target / "documents.lance" / "_versions" / "1.manifest").is_file()
    # No bucket or key directory was created inside the cache.
    assert not (target / BUCKET).exists()
    assert not (target / "optimization").exists()


def test_a_prefix_with_no_files_fails_loudly(tmp_path: Path) -> None:
    fake = _FakeS3({})
    with pytest.raises(FileNotFoundError, match="No index files found"):
        copy_index_tree(URI, tmp_path / "empty", filesystem=fake)


def test_a_non_s3_remote_uri_is_refused_rather_than_misread(tmp_path: Path) -> None:
    assert is_remote_index_uri("gs://bucket/key")
    with pytest.raises(ValueError, match="Only s3://"):
        copy_index_tree("gs://bucket/key", tmp_path / "gs")


def _remote_config() -> RAGConfig:
    config = RAGConfig()
    config.retrieval.lancedb_index_dir = URI
    config.retrieval.load_if_exists = True
    config.retrieval_only = True
    config.data = DataLoaderConfig(path_or_repo="dev.jsonl")
    return config


def test_the_run_directory_cache_uses_the_same_bucket_and_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`local_index_for` routes the default `<run_dir>/idx-cache` through the copy."""
    fake = _FakeS3(_index_objects())
    monkeypatch.setattr("deepfix_rag.index_cache.s3_filesystem", lambda: fake)

    opt = HyperparameterOpt(
        base_config=_remote_config(),
        study_name="bucket_test",
        run_dir=tmp_path / "run",
        persist=False,
    )
    cache = opt.local_index_for()

    assert cache == tmp_path / "run" / "idx-cache"
    assert fake.selectors == [(f"{BUCKET}/{KEY}", True)]
    assert opt.index_copy_source == URI
    assert (cache / "deepfix_index.json").is_file()
    # Every trial then reads the cache, never the object store.
    overrides = opt._base_overrides()
    assert overrides["retrieval.lancedb_index_dir"] == str(cache)
    # A second call reuses the copy instead of listing S3 again.
    assert opt.local_index_for() == cache
    assert fake.selectors == [(f"{BUCKET}/{KEY}", True)]


def test_an_explicit_index_dir_is_filled_from_the_same_bucket_and_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeS3(_index_objects())
    monkeypatch.setattr("deepfix_rag.index_cache.s3_filesystem", lambda: fake)

    explicit = tmp_path / "run" / "my-idx"
    opt = HyperparameterOpt(
        base_config=_remote_config(),
        study_name="explicit_bucket_test",
        run_dir=tmp_path / "run",
        index_dir=explicit,
        persist=False,
    )
    assert opt.local_index_for() == explicit
    assert fake.selectors == [(f"{BUCKET}/{KEY}", True)]
    assert (explicit / "documents.lance" / "data" / "0.arrow").is_file()


def test_the_local_cache_copy_is_skipped_when_it_is_already_populated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "run" / "idx-cache"
    (cache / "documents.lance").mkdir(parents=True)
    (cache / "deepfix_index.json").write_text("{}", encoding="utf-8")

    def _explode() -> object:
        raise AssertionError("the object store must not be touched")

    monkeypatch.setattr("deepfix_rag.index_cache.s3_filesystem", _explode)
    opt = HyperparameterOpt(
        base_config=_remote_config(),
        study_name="reuse_test",
        run_dir=tmp_path / "run",
        persist=False,
    )
    assert opt.local_index_for() == cache


def test_the_localized_copy_carries_the_manifest_a_later_eval_needs(tmp_path: Path) -> None:
    """An object-store index may have no manifest; the cache must get one.

    Without it `deepfix eval --index-dir` raises stale_index and the round falls
    back to reading S3, which is what happened in HPO-007.
    """
    from deepfix_rag.index_manifest import assert_index_current, read_index_manifest

    fake = _FakeS3(_index_objects())
    target = tmp_path / "idx-cache"
    config = _remote_config()

    copy_index_tree(URI, target, filesystem=fake)
    # The S3 tree in this test has a manifest, but a real one may not; drop it
    # to model the object store exactly.
    (target / "deepfix_index.json").unlink()

    localize_index(URI, target, config, filesystem=fake)

    manifest = read_index_manifest(target)
    assert manifest is not None
    assert manifest["localized_from"] == URI
    assert manifest["signature"] == index_time_signature(config)
    # The whole point: a later eval of this config accepts the cache.
    assert_index_current(target, config)


def test_a_cache_without_a_manifest_is_still_localized(tmp_path: Path) -> None:
    """Only the LanceDB payload needs to come from S3 for the copy to succeed."""
    from deepfix_rag.index_manifest import assert_index_current

    objects = {
        f"{KEY}/documents.lance/data/0.arrow": b"arrow-bytes",
    }
    fake = _FakeS3(objects)
    target = tmp_path / "idx-cache"
    config = _remote_config()

    localize_index(URI, target, config, filesystem=fake)

    assert (target / "deepfix_index.json").is_file()
    assert_index_current(target, config)


def test_re_localizing_records_the_manifest_it_replaced(tmp_path: Path) -> None:
    """A cache that already has a manifest keeps its previous hash as provenance."""
    from deepfix_rag.index_manifest import read_index_manifest

    fake = _FakeS3(_index_objects())
    target = tmp_path / "idx-cache"
    config = _remote_config()

    localize_index(URI, target, config, filesystem=fake)
    first = read_index_manifest(target)
    assert first is not None
    # The object store supplied another host's manifest, so this one replaced it
    # and keeps the old hash as provenance.
    assert first["replaced_signature_hash"] == ORIGIN_MANIFEST["signature_hash"]
    assert first["signature_hash"] != ORIGIN_MANIFEST["signature_hash"]

    localize_index(URI, target, config, filesystem=fake)
    second = read_index_manifest(target)
    assert second is not None
    # Re-copying brings the origin manifest again, so provenance still points at
    # the object store rather than at the manifest this host wrote.
    assert second["signature_hash"] == first["signature_hash"]
    assert second["replaced_signature_hash"] == ORIGIN_MANIFEST["signature_hash"]


def test_is_remote_and_populated_helpers(tmp_path: Path) -> None:
    assert is_remote_index_uri("s3://bucket/key")
    assert not is_remote_index_uri("/local/path")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert index_dir_populated(empty) is False
    (empty / "x").write_text("y", encoding="utf-8")
    assert index_dir_populated(empty) is True

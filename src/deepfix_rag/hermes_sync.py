"""Fixed sync command for the Hermes sandbox.

``deepfix-sync download`` copies ``input/`` from the run prefix into the
container work directory. ``deepfix-sync upload`` writes ``output/result.json``
and, when the agent produced one, ``output/candidate.yaml``. The command is
this module. The agent does not write a replacement script.

Inside the container the S3 credentials are the ``hermes-optimizer`` key,
already exposed as ``S3_ACCESS_KEY_ID`` and ``S3_SECRET_ACCESS_KEY``. The
root key is not read. ``DEEPFIX_BUNDLE_ROOT`` selects a local directory
layout for tests.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from botocore.exceptions import ClientError

from deepfix_rag.run_bundle import (
    CONTAINER_WORK_ROOT,
    HPO_SPACE_FILENAME,
    DirectoryObjectStore,
    ObjectStore,
    RecordingObjectStore,
    RunBundleError,
    S3ObjectStore,
    StoreRequest,
    bucket_from_s3_uri,
    candidate_index_uri,
    container_work_dir,
    run_prefix,
    validate_run_id,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_RUNTIME = 2
_INPUT_DIR = "input"
_OUTPUT_RESULT = "output/result.json"
_OUTPUT_CANDIDATE = "output/candidate.yaml"
_OUTPUT_HEARTBEAT = "output/heartbeat.json"
_OUTPUT_REPORTS = ("state.json", "baseline_smoke.json")


def download_input(
    store: ObjectStore,
    *,
    bucket: str,
    run_id: str,
    work_dir: Path,
) -> tuple[StoreRequest, ...]:
    """Download ``input/`` for one run into ``work_dir``.

    Objects outside that run's ``input/`` prefix are not fetched.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{run_prefix(run_id)}/{_INPUT_DIR}/"
    recorder = RecordingObjectStore(store)
    for key in recorder.list(bucket, prefix):
        if not key.startswith(prefix):
            continue
        relative = key[len(run_prefix(run_id)) + 1 :]
        target = _safe_target(work_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(recorder.get(bucket, key))
    return tuple(recorder.requests)


def upload_output(
    store: ObjectStore,
    *,
    bucket: str,
    run_id: str,
    result_path: Path,
    candidate_path: Path | None = None,
) -> tuple[StoreRequest, ...]:
    """Upload the result file and the candidate YAML to ``output/``."""
    if not result_path.is_file():
        raise RunBundleError("result file is missing")
    prefix = run_prefix(run_id)
    recorder = RecordingObjectStore(store)
    recorder.put(bucket, f"{prefix}/{_OUTPUT_RESULT}", result_path.read_bytes())
    for name, path in _report_files(result_path):
        recorder.put(bucket, f"{prefix}/output/{name}", path.read_bytes())
    if candidate_path is not None:
        if not candidate_path.is_file():
            raise RunBundleError("candidate file is missing")
        recorder.put(bucket, f"{prefix}/{_OUTPUT_CANDIDATE}", candidate_path.read_bytes())
    return tuple(recorder.requests)


def download_result(
    store: ObjectStore,
    *,
    bucket: str,
    run_id: str,
    destination: Path,
) -> bool:
    """Copy ``output/result.json`` onto ``destination``.

    Returns False when that object is absent. The API calls this with the
    root key and then reads the local file from ``finish_run``. A missing
    object is not an error here. Other store failures propagate, and their
    text is not rewritten.
    """
    key = f"{run_prefix(run_id)}/{_OUTPUT_RESULT}"
    try:
        body = store.get(bucket, key)
    except KeyError:
        return False
    except ClientError as exc:
        if not _missing_object(exc):
            raise
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    for name in _OUTPUT_REPORTS:
        _copy_optional(
            store,
            bucket=bucket,
            key=f"{run_prefix(run_id)}/output/{name}",
            destination=destination.parent / name,
        )
    return True


def _report_files(result_path: Path) -> list[tuple[str, Path]]:
    """``state.json`` and ``baseline_smoke.json`` beside the result, if present.

    The file next to ``result.json`` wins. The parent directory is the fallback
    for a report written in the run directory rather than ``output/``.
    """
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for directory in (result_path.parent, result_path.parent.parent):
        for name in _OUTPUT_REPORTS:
            if name in seen:
                continue
            path = directory / name
            if not path.is_file():
                continue
            seen.add(name)
            found.append((name, path))
    return found


def _copy_optional(
    store: ObjectStore,
    *,
    bucket: str,
    key: str,
    destination: Path,
) -> None:
    try:
        body = store.get(bucket, key)
    except KeyError:
        return
    except ClientError as exc:
        if not _missing_object(exc):
            raise
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


def heartbeat_timestamp(
    store: ObjectStore,
    *,
    bucket: str,
    run_id: str,
) -> datetime | None:
    """When ``output/heartbeat.json`` was last written, or None if it is absent.

    The newer of the object's modified time and a ``written_at`` field wins.
    A missing object is None. Other store failures propagate.
    """
    key = f"{run_prefix(run_id)}/{_OUTPUT_HEARTBEAT}"
    try:
        modified = store.modified_at(bucket, key)
    except KeyError:
        modified = None
    except ClientError as exc:
        if not _missing_object(exc):
            raise
        modified = None
    written = _heartbeat_written_at(store, bucket, key)
    if modified is None:
        return written
    if written is None:
        return modified
    return max(modified, written)


def read_heartbeat(
    store: ObjectStore,
    *,
    bucket: str,
    run_id: str,
) -> dict[str, Any] | None:
    """The JSON object in ``output/heartbeat.json``, or None when it is absent.

    A missing object and a body that is not a JSON object are None. Other
    store failures propagate.
    """
    key = f"{run_prefix(run_id)}/{_OUTPUT_HEARTBEAT}"
    try:
        body = store.get(bucket, key)
    except KeyError:
        return None
    except ClientError as exc:
        if not _missing_object(exc):
            raise
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _heartbeat_written_at(store: ObjectStore, bucket: str, key: str) -> datetime | None:
    try:
        body = store.get(bucket, key)
    except KeyError:
        return None
    except ClientError as exc:
        if not _missing_object(exc):
            raise
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("written_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _missing_object(exc: ClientError) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return error.get("Code") in {"NoSuchKey", "404", "NotFound"}


def describe_download(bucket: str, run_id: str, work_dir: Path) -> str:
    """Paths the run instructions use. No credentials."""
    lines = (
        f"work_dir={work_dir}",
        f"champion_config={work_dir / _INPUT_DIR / 'champion.yaml'}",
        f"dev_file={work_dir / _INPUT_DIR / 'feedback_dev.jsonl'}",
        f"corpus={work_dir / _INPUT_DIR / 'corpus'}",
        f"champion_index={work_dir / _INPUT_DIR / 'champion-index'}",
        f"hpo_space={work_dir / _INPUT_DIR / HPO_SPACE_FILENAME}",
        f"candidate_index_uri={candidate_index_uri(bucket, run_id)}",
    )
    return "\n".join(lines) + "\n"


def store_from_env(env: Mapping[str, str]) -> ObjectStore:
    """Directory layout when ``DEEPFIX_BUNDLE_ROOT`` is set, otherwise S3."""
    root = env.get("DEEPFIX_BUNDLE_ROOT", "").strip()
    if root:
        return DirectoryObjectStore(Path(root))
    missing = [
        name
        for name in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_URI")
        if not env.get(name, "").strip()
    ]
    if missing:
        raise RunBundleError("missing " + ", ".join(missing))
    return S3ObjectStore(
        endpoint_url=env["S3_ENDPOINT_URL"].strip(),
        access_key_id=env["S3_ACCESS_KEY_ID"].strip(),
        secret_access_key=env["S3_SECRET_ACCESS_KEY"].strip(),
        region=env.get("S3_REGION", "").strip() or "us-east-1",
    )


def bucket_from_env(env: Mapping[str, str]) -> str:
    """Bucket from ``S3_URI``. The value is required."""
    uri = env.get("S3_URI", "").strip()
    if not uri:
        raise RunBundleError("missing S3_URI")
    return bucket_from_s3_uri(uri)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``deepfix-sync`` command."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return EXIT_OK
        return EXIT_USAGE
    env = os.environ
    try:
        store = store_from_env(env)
        bucket = bucket_from_env(env)
        if args.command == "download":
            work_dir = container_work_dir(args.run_id, args.work_root)
            download_input(store, bucket=bucket, run_id=args.run_id, work_dir=work_dir)
            _write_run_marker(args.work_root, args.run_id)
            sys.stdout.write(describe_download(bucket, args.run_id, work_dir))
            return EXIT_OK
        upload_output(
            store,
            bucket=bucket,
            run_id=args.run_id,
            result_path=args.result,
            candidate_path=args.candidate,
        )
        return EXIT_OK
    except RunBundleError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:
        print(f"deepfix-sync failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_RUNTIME


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepfix-sync",
        description="Download a run bundle input and upload its output.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download", help="Download input/ into the work directory")
    download.add_argument("--run-id", required=True)
    download.add_argument("--work-root", type=Path, default=CONTAINER_WORK_ROOT)
    upload = sub.add_parser("upload", help="Upload result.json and the candidate YAML")
    upload.add_argument("--run-id", required=True)
    upload.add_argument("--result", type=Path, required=True)
    upload.add_argument("--candidate", type=Path, default=None)
    return parser


def _write_run_marker(work_root: Path, run_id: str) -> None:
    """Remember the run id where the shim can read it without parsing argv."""
    checked = validate_run_id(run_id)
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / ".deepfix-run-id").write_text(checked + "\n", encoding="utf-8")


def _safe_target(work_dir: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        raise RunBundleError("refusing to write outside the work directory")
    root = work_dir.resolve()
    target = root.joinpath(*relative.split("/")).resolve()
    if not target.is_relative_to(root):
        raise RunBundleError("refusing to write outside the work directory")
    return target


if __name__ == "__main__":
    raise SystemExit(main())

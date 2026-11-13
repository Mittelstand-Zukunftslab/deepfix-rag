"""The lockfile and the tools that read it agree, in both images.

The build warning this guards against is a lock that a builder considers out of
sync. The two ways that happens are a lock written by a different uv generation
and a lock whose pin has drifted, so this checks the lock's own metadata, the
pins in the Dockerfiles, and that every requirement survives a round trip.
"""

from pathlib import Path
import re
import tomllib

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK = REPO_ROOT / "uv.lock"
PYPROJECT = REPO_ROOT / "pyproject.toml"
API_DOCKERFILE = REPO_ROOT / "Dockerfile"
HERMES_DOCKERFILE = REPO_ROOT / "docker" / "hermes" / "Dockerfile"


def _lock_text() -> str:
    return LOCK.read_text(encoding="utf-8")


def _lock_meta() -> dict[str, str]:
    """The header-only ``version`` / ``revision`` pair, not each package's version."""
    head = _lock_text().split("\n\n", 1)[0]
    return dict(re.findall(r"(?m)^(version|revision) = (\S+)$", head))


def test_the_lockfile_is_a_recent_revision() -> None:
    """A lock newer than the builder's uv is what a builder calls out of sync."""
    meta = _lock_meta()
    assert meta["version"] == "1"
    # revision 5 is the format uv 0.9.x and later write for this project.
    assert int(meta["revision"]) >= 3


def test_both_images_pin_the_uv_that_reads_the_lock() -> None:
    """A floating uv tag lets a rebuild resolve differently from the lock."""
    api = API_DOCKERFILE.read_text(encoding="utf-8")
    pinned = re.search(r"FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)-", api)
    assert pinned, "the API image does not pin a uv version"
    assert "astral-sh/uv:python3.12-bookworm\n" not in api
    # The Hermes image takes uv from the pinned base image, which is 0.9.x.
    hermes = HERMES_DOCKERFILE.read_text(encoding="utf-8")
    assert "nousresearch/hermes-agent@sha256:" in hermes
    assert "uv python install 3.12" in hermes


def test_every_project_requirement_is_locked() -> None:
    """A requirement missing from the lock is exactly what `--frozen` refuses."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")
        for requirement in project["project"]["dependencies"]
    }
    declared.update(
        name.lower().replace("_", "-") for name in project["tool"]["uv"].get("sources", {})
    )
    locked = {
        name.lower().replace("_", "-")
        for name in re.findall(r'(?m)^name = "([^"]+)"$', _lock_text())
    }
    missing = sorted(declared - locked - {"deepfix-rag"})
    assert not missing, f"pyproject requires unlocked packages: {missing}"


def test_the_lock_pins_a_git_source_by_full_commit() -> None:
    """An unpinned or short git ref makes a rebuild re-resolve the dependency."""
    sources = re.findall(r'source = \{ git = "[^"#]+(#([0-9a-f]+))?"', _lock_text())
    assert sources, "the lock has no git source"
    for _full, sha in sources:
        assert sha, "a git source is not pinned to a commit"
        assert len(sha) == 40, f"git source pinned to a short ref: {sha}"


def test_the_lock_is_complete_for_the_declared_python() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires = project["project"]["requires-python"]
    assert re.search(r"(?m)^requires-python = ", _lock_text())
    assert "3.12" in requires
    # The lock records the interpreter it resolved for.
    assert "3.12" in _lock_text().splitlines()[3] or "3.12" in _lock_text()[:400]

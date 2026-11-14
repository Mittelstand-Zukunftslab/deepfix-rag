"""The Hermes Dockerfile parses, and every instruction is well formed.

A build on the server failed because two ``COPY`` instructions ended up on one
line: ``COPY src /dstCOPY other /dst``. Docker reports that as a missing source
path, long after the mistake, so these checks run on the file itself.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = (
    REPO_ROOT / "docker" / "hermes" / "Dockerfile",
    REPO_ROOT / "Dockerfile",
)

# A line that starts with an instruction keyword. A merged instruction shows up
# as a second keyword glued to a path, with no whitespace before it.
_INSTRUCTION = re.compile(
    r"^\s*(FROM|RUN|CMD|LABEL|MAINTAINER|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|"
    r"VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b"
)
_GLUED_KEYWORD = re.compile(
    r"\S(COPY|ADD|RUN|ENV|ARG|LABEL|WORKDIR|USER|ENTRYPOINT|CMD|VOLUME|EXPOSE|SHELL|ONBUILD)\s"
)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Instruction lines with backslash continuations joined, numbered by first line."""
    joined: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer.append(stripped[:-1])
            continue
        buffer.append(stripped)
        joined.append((start, " ".join(buffer)))
        buffer = []
    if buffer:
        joined.append((start, " ".join(buffer)))
    return joined


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_no_two_instructions_are_glued_onto_one_line(dockerfile: Path) -> None:
    """`COPY a /xCOPY b /y` parses as one COPY with a bogus source."""
    for number, line in _logical_lines(dockerfile.read_text(encoding="utf-8")):
        keyword = _INSTRUCTION.match(line)
        if keyword is None or keyword.group(1) in {"RUN", "SHELL"}:
            # RUN bodies contain shell words that look like keywords.
            continue
        glued = _GLUED_KEYWORD.search(line)
        assert glued is None, (
            f"{dockerfile.name}:{number} has a second instruction glued to a path: {line}"
        )


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_every_copy_and_add_source_exists(dockerfile: Path) -> None:
    """A COPY from a path that is not in the build context fails the whole build."""
    missing: list[str] = []
    for number, line in _logical_lines(dockerfile.read_text(encoding="utf-8")):
        match = re.match(r"^(COPY|ADD)\s+(.*)$", line)
        if match is None:
            continue
        arguments = [part for part in match.group(2).split() if not part.startswith("--")]
        if len(arguments) < 2:
            missing.append(f"{dockerfile.name}:{number} has too few arguments: {line}")
            continue
        for source in arguments[:-1]:
            if source in {".", ".."} or source.startswith(("http://", "https://", "git@")):
                continue
            if not (REPO_ROOT / source).exists():
                missing.append(f"{dockerfile.name}:{number} source does not exist: {source}")
    assert not missing, missing


def test_the_hermes_image_carries_the_shipped_hpo_space() -> None:
    """The sandbox has no repository checkout, so the space must be copied in."""
    dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY configs/hpo_retrieval_small.yaml /opt/deepfix/configs/hpo_retrieval_small.yaml"
        in (dockerfile)
    )


def test_the_hpo_skill_confirmation_uses_the_bundle_champion() -> None:
    """`configs/champion.yaml` does not exist in the sandbox; the bundle's copy does."""
    text = (REPO_ROOT / ".agents" / "skills" / "hpo-search" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    compare_commands = [
        line for line in text.splitlines() if "deepfix eval" in line and "--compare" in line
    ]
    assert compare_commands, "hpo-search has no confirmation command"
    for command in compare_commands:
        assert "--compare <run_dir>/input/champion.yaml" in command, command
        assert "--compare configs/champion.yaml" not in command, command

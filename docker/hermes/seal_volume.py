"""Prepare an existing Hermes data volume for this sealed image.

Stage2 refreshes the dependency stamp before the sandbox boot runs. On a
volume left by an older image that pip-installed hermes-otel into the Hermes
venv, that refresh sees a plugin with ``python_dependencies`` or a venv stamp
recorded for that member set. The sealed image cannot rebuild the artifact, so
startup prints ``venv: out of sync with uv.lock``.

This runs first. It points the managed plugins at the copies in the image,
which are ``python_runtime: external``, and drops a venv stamp that is not the
running image's stamp. A fresh volume has neither, so it is left alone.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil

HERMES_HOME_ENV = "HERMES_HOME"
DEFAULT_HERMES_HOME = Path("/opt/data")
OTEL_PLUGIN = "hermes_otel"
TAG_PLUGIN = "deepfix-run-tag"
DEFAULT_OTEL = Path("/opt/deepfix/otel/hermes_otel")
DEFAULT_TAG = Path("/opt/deepfix/plugins/deepfix-run-tag")


def force_plugin_link(home: Path, name: str, source: Path) -> None:
    """Make ``home/plugins/<name>`` a symlink to ``source``.

    A directory left by an older install is replaced. An unrelated missing
    source is ignored. The link target is not followed.
    """
    if not (source / "plugin.yaml").is_file() or not (source / "__init__.py").is_file():
        return
    dest_root = home / "plugins"
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / name
    if dest.is_symlink() and dest.resolve() == source.resolve():
        return
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    dest.symlink_to(source)


def forget_venv_stamp(path: Path, *, current: bool) -> bool:
    """Delete a recorded venv stamp that is not this image's stamp.

    ``current`` is the image's own check. A fresh volume has no file and is
    kept. A matching stamp is kept so stage2 does not rebuild extras.
    """
    if current or not path.is_file():
        return False
    path.unlink()
    return True


def _stamp_is_current() -> bool:
    """True when the volume stamp matches this image. Unreadable means stale."""
    try:
        from pm.install import venv_is_current
        from pm.paths import repo_root
    except ImportError:
        return True
    try:
        return bool(venv_is_current(project_root=repo_root()))
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


def _stamp_path() -> Path | None:
    try:
        from pm.environments import runtime_facts_path
        from pm.paths import repo_root
    except ImportError:
        return None
    return runtime_facts_path(repo_root())


def seal_existing_volume(home: Path, *, otel: Path, tag: Path) -> bool:
    """Relink managed plugins and drop a stale venv stamp. Return True if dropped."""
    home.mkdir(parents=True, exist_ok=True)
    force_plugin_link(home, OTEL_PLUGIN, otel)
    force_plugin_link(home, TAG_PLUGIN, tag)
    path = _stamp_path()
    if path is None:
        return False
    return forget_venv_stamp(path, current=_stamp_is_current())


def main() -> None:
    """Entrypoint for the cont-init script that runs before stage2."""
    home = Path(os.environ.get(HERMES_HOME_ENV) or DEFAULT_HERMES_HOME)
    seal_existing_volume(home, otel=DEFAULT_OTEL, tag=DEFAULT_TAG)


if __name__ == "__main__":
    main()

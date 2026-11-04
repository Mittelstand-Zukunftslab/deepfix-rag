"""Seed the Hermes optimization sandbox on container boot.

Runs as root from s6 cont-init, after the official stage2 hook and before
the profile reconciler. Secrets are read from the container environment and
written into the profile ``.env``. They are never embedded in the image.

The pinned image (Hermes v0.22.7) refuses ``API_SERVER_KEY`` values shorter
than 16 characters. Its terminal foreground cap defaults to 600 seconds
(``TERMINAL_MAX_FOREGROUND_TIMEOUT``) and ``timeouts.tools.sequential_call``
defaults to 420 seconds. Both would cut off ``deepfix index`` or
``deepfix eval`` before ``terminal.timeout`` of 7200 seconds, so this script
raises them with the sandbox config.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import pwd
from typing import Any

import yaml

HERMES_HOME_ENV = "HERMES_HOME"
DEFAULT_HERMES_HOME = Path("/opt/data")
DEFAULT_SKILLS_DIR = Path("/opt/deepfix/skills")
MODEL_PROVIDER = "openrouter"
MODEL_DEFAULT = "anthropic/claude-haiku-5.5"
MODEL_BASE_URL = "https://openrouter.ai/api/v1"
# Starting point from the PRD. Confirmed on v0.22.7: the platform clamp is
# 365 days on Linux, so 7200 is accepted. The foreground cap and the
# sequential tool deadline must be raised separately (see below).
TERMINAL_TIMEOUT_SECONDS = 7200
# Slightly above the terminal timeout so the tool executor does not abandon
# the call before the terminal's own deadline reports it.
TOOL_DEADLINE_SECONDS = 7500
FOREGROUND_TIMEOUT_ENV = "TERMINAL_MAX_FOREGROUND_TIMEOUT"
# The image's startup guard uses 16. The PRD's docs minimum of 8 is not
# enough for this pin: a shorter key never opens the API server.
MIN_API_KEY_LENGTH = 16
CHAT_PROFILE = "chat"
CHAT_API_PORT = 8643
CHAT_API_KEY_ENV = "HERMES_CHAT_API_SERVER_KEY"

# Documented approvals.deny globs for force-push and pipe-to-shell.
# ``approvals.mode: off`` does not bypass this list.
DENY_PATTERNS: tuple[str, ...] = (
    "git push --force*",
    "git push --force-with-lease*",
    "*curl*|*sh*",
    "*wget*|*sh*",
    "*curl*|*bash*",
    "*wget*|*bash*",
)

# Chat may not run commands or write files. These are the v0.22.7 toolset
# names from cli-config.yaml.example. code_execution is omitted because it
# is not in that list; terminal and file are the command and write toolsets.
CHAT_API_TOOLSETS: tuple[str, ...] = ("skills", "web", "vision", "todo", "memory")

_PLACEHOLDER_KEYS = frozenset(
    {
        "change-me-please",
        "change-me-local-dev",
        "your_api_key_here",
        "your_key_here",
        "changeme",
        "password",
        "secret",
    }
)

_DEFAULT_ENV_UPDATES = (
    "API_SERVER_ENABLED",
    "API_SERVER_HOST",
    "API_SERVER_KEY",
    "OPENROUTER_API_KEY",
    FOREGROUND_TIMEOUT_ENV,
)
# Never leave the product key, a dashboard enablement, or a shared port in
# the optimization profile. API_SERVER_PORT stays unset so this profile binds
# 8642 and the chat profile can bind 8643.
_STRIPPED_ENV_KEYS = frozenset({"LLM_API_KEY", "HERMES_DASHBOARD", "API_SERVER_PORT"})

_S6_ENV_DIRS = (
    Path("/run/s6/container_environment"),
    Path("/var/run/s6/container_environment"),
)


class SandboxBootError(ValueError):
    """The sandbox cannot start with this environment or filesystem."""


def runtime_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Container env, filled from s6's env dir when a value is missing.

    ``/init`` keeps cont-init's environment, but a scrubbed or empty value
    must not hide the key Compose injected. s6 stores each variable as a
    file under ``container_environment``.
    """
    env = dict(os.environ if base is None else base)
    for directory in _S6_ENV_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not path.is_file() or env.get(path.name):
                continue
            env[path.name] = path.read_text(encoding="utf-8").rstrip("\n")
    return env


def validate_api_key(value: str, *, name: str) -> str:
    """Return ``value`` when it meets the pinned image's key floor."""
    key = value.strip()
    folded = key.casefold()
    too_short = len(key) < MIN_API_KEY_LENGTH
    placeholder = folded in _PLACEHOLDER_KEYS or folded.startswith("change-me")
    if too_short or placeholder or any(char in key for char in "\n\r\x00"):
        raise SandboxBootError(
            f"{name} is required and must be at least {MIN_API_KEY_LENGTH} "
            "characters and not a placeholder. Generate one with "
            "`openssl rand -hex 32`. The pinned Hermes image rejects shorter keys."
        )
    return key


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        current = base.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _merge(current, value)
        else:
            base[key] = value
    return base


def optimization_config_patch(skills_dir: Path) -> dict[str, Any]:
    """Config keys this sandbox owns. Other seeded keys are left in place."""
    return {
        "model": {
            "provider": MODEL_PROVIDER,
            "default": MODEL_DEFAULT,
            "base_url": MODEL_BASE_URL,
        },
        "terminal": {
            "backend": "local",
            "timeout": TERMINAL_TIMEOUT_SECONDS,
            "cwd": "/opt/data/workspace",
        },
        "approvals": {
            "mode": "off",
            "deny": list(DENY_PATTERNS),
        },
        "skills": {
            "external_dirs": [str(skills_dir)],
        },
        "timeouts": {
            "tools": {
                "sequential_call": TOOL_DEADLINE_SECONDS,
                "concurrent_batch": TOOL_DEADLINE_SECONDS,
            }
        },
    }


def chat_profile_config(config_version: object) -> dict[str, Any]:
    """A second profile that is not the optimization agent.

    Approval mode stays ``smart``. Terminal and file toolsets are absent.
    The optimization skill directory is not attached.
    """
    config: dict[str, Any] = {
        "model": {
            "provider": MODEL_PROVIDER,
            "default": MODEL_DEFAULT,
            "base_url": MODEL_BASE_URL,
        },
        "approvals": {"mode": "smart"},
        "terminal": {"backend": "local", "timeout": 180},
        "platform_toolsets": {"api_server": list(CHAT_API_TOOLSETS)},
    }
    if isinstance(config_version, int):
        config["_config_version"] = config_version
    return config


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise SandboxBootError(f"{path} is not a YAML mapping")
    return loaded


def _dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    # SafeDumper quotes YAML 1.1 booleans such as ``off``, so approvals.mode
    # stays the string the Hermes loader expects.
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    path.chmod(0o640)


def _hermes_uid() -> tuple[int, int] | None:
    try:
        record = pwd.getpwnam("hermes")
    except KeyError:
        return None
    return record.pw_uid, record.pw_gid


def _chown_hermes(path: Path) -> None:
    identity = _hermes_uid()
    if identity is None:
        return
    os.chown(path, identity[0], identity[1])


def _assert_skills_locked(skills_dir: Path) -> None:
    if not skills_dir.is_dir():
        raise SandboxBootError(f"skills directory is missing: {skills_dir}")
    identity = _hermes_uid()
    for path in (skills_dir, *skills_dir.rglob("*")):
        mode = path.stat().st_mode
        if mode & 0o022:
            raise SandboxBootError(
                f"{path} is writable by group or other; skills must be root-owned "
                "and not writable by the hermes user"
            )
        if identity is not None and path.stat().st_uid == identity[0]:
            raise SandboxBootError(f"{path} is owned by the hermes user")


def _upsert_env(
    path: Path,
    updates: dict[str, str],
    remove: frozenset[str],
    *,
    replace: bool = False,
) -> None:
    lines: list[str] = []
    if path.exists() and not replace:
        lines = path.read_text(encoding="utf-8").splitlines()
    managed = set(updates) | set(remove)
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        key = stripped.split("=", 1)[0]
        if key in managed:
            continue
        kept.append(line)
    kept.extend(f"{key}={value}" for key, value in updates.items())
    text = "\n".join(kept)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    _chown_hermes(path)


def _reject_product_key(env: Mapping[str, str]) -> None:
    if env.get("LLM_API_KEY", "").strip():
        raise SandboxBootError(
            "LLM_API_KEY must not be present in the Hermes environment. "
            "Model calls use OPENROUTER_API_KEY."
        )


def apply_sandbox(
    home: Path,
    env: Mapping[str, str],
    skills_dir: Path,
) -> None:
    """Write the optimization config and the stopped chat profile."""
    _reject_product_key(env)
    _assert_skills_locked(skills_dir)
    api_key = validate_api_key(env.get("API_SERVER_KEY", ""), name="API_SERVER_KEY")
    chat_key_raw = env.get(CHAT_API_KEY_ENV, "").strip()
    chat_key = validate_api_key(chat_key_raw, name=CHAT_API_KEY_ENV) if chat_key_raw else ""

    home.mkdir(parents=True, exist_ok=True)
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _chown_hermes(workspace)

    config_path = home / "config.yaml"
    config = _load_yaml(config_path)
    _merge(config, optimization_config_patch(skills_dir))
    _dump_yaml(config_path, config)
    _chown_hermes(config_path)

    updates: dict[str, str] = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_KEY": api_key,
        FOREGROUND_TIMEOUT_ENV: str(TERMINAL_TIMEOUT_SECONDS),
    }
    openrouter_key = env.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        if any(char in openrouter_key for char in "\n\r\x00"):
            raise SandboxBootError("OPENROUTER_API_KEY contains a newline or NUL")
        updates["OPENROUTER_API_KEY"] = openrouter_key
    _upsert_env(home / ".env", updates, _STRIPPED_ENV_KEYS)

    chat_dir = home / "profiles" / CHAT_PROFILE
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_config = chat_profile_config(config.get("_config_version"))
    _dump_yaml(chat_dir / "config.yaml", chat_config)
    _chown_hermes(chat_dir / "config.yaml")
    soul = chat_dir / "SOUL.md"
    soul.write_text(
        "# Chat profile\n\n"
        "This profile is the future admin chat. It is not the optimization "
        "agent. It has no terminal toolset and no file-write toolset, and it "
        "does not receive the optimization profile's OpenRouter key.\n",
        encoding="utf-8",
    )
    soul.chmod(0o644)
    _chown_hermes(soul)
    chat_env: dict[str, str] = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_PORT": str(CHAT_API_PORT),
    }
    if chat_key:
        chat_env["API_SERVER_KEY"] = chat_key
    # Replace the file so an optimization key from an older boot cannot linger.
    _upsert_env(chat_dir / ".env", chat_env, frozenset(), replace=True)
    state = chat_dir / "gateway_state.json"
    state.write_text('{"gateway_state":"stopped"}\n', encoding="utf-8")
    state.chmod(0o644)
    _chown_hermes(state)
    _chown_hermes(chat_dir)


def main() -> None:
    """Entrypoint used by the image's cont-init script."""
    env = runtime_env()
    home = Path(env.get(HERMES_HOME_ENV) or DEFAULT_HERMES_HOME)
    skills_dir = Path(env.get("DEEPFIX_SKILLS_DIR") or DEFAULT_SKILLS_DIR)
    try:
        apply_sandbox(home, env, skills_dir)
    except SandboxBootError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

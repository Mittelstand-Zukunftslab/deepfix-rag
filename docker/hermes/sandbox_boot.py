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

LanceDB uses the ``S3_*`` variables the CLI already reads. Those values are
the ``hermes-optimizer`` key (Compose maps ``HERMES_S3_ACCESS_KEY_ID`` onto
``S3_ACCESS_KEY_ID``). The root key must not be in this environment. The
chat profile does not receive the S3 settings.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import pwd
import sys
import time
from typing import Any

import yaml

HERMES_HOME_ENV = "HERMES_HOME"
DEFAULT_HERMES_HOME = Path("/opt/data")
DEFAULT_SKILLS_DIR = Path("/opt/deepfix/skills")
# Outside the sealed Hermes venv. A plugin with python_dependencies becomes a
# uv workspace member and the image then reports the venv out of sync with
# uv.lock. The package is installed under this directory instead.
DEFAULT_HERMES_OTEL_PACKAGE = Path("/opt/deepfix/otel/hermes_otel")
DEFAULT_RUN_TAG_PLUGIN = Path("/opt/deepfix/plugins/deepfix-run-tag")
HERMES_OTEL_PLUGIN = "hermes_otel"
RUN_TAG_PLUGIN = "deepfix-run-tag"
MODEL_PROVIDER = "openrouter"
MODEL_PROVIDER_ENV = "HERMES_MODEL_PROVIDER"
# The model id is configuration, not code. Compose passes HERMES_MODEL and the
# boot fails when it is missing instead of seeding a repository-wide default.
MODEL_ENV = "HERMES_MODEL"
MODEL_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_BASE_URL_ENV = "HERMES_MODEL_BASE_URL"
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

# Starting point from the PRD. ``skill_view`` calls consume turns, so 40
# covers one skill load plus one hypothesis. The example config on this pin
# ships ``agent.max_turns: 500`` (``HERMES_MAX_ITERATIONS``). The gateway
# bridges this key into that env var for the launch profile.
AGENT_MAX_TURNS = 40
# Where the agent may create a new skill. The shipped skills are read-only, so a
# write has to land somewhere the runtime user owns: under the data volume.
SKILL_CREATE_DIRNAME = Path("agent-skills")


def skill_create_dir(home: Path | None = None) -> Path:
    """Writable directory for skills the agent creates, under the Hermes home.

    ``/opt/deepfix/skills`` stays root-owned mode 0444 by design, and
    ``$HERMES_HOME`` (the ``hermes_data`` volume) is the runtime user's. A skill
    write therefore has a writable target that is not the shipped tree.
    """
    base = home if home is not None else DEFAULT_HERMES_HOME
    return base / SKILL_CREATE_DIRNAME


# GET /v1/toolsets on nousresearch/hermes-agent v0.22.7 lists
# hermes_cli/tools_config.py CONFIGURABLE_TOOLSETS and sets ``enabled`` from
# platform_toolsets.api_server. Names below are that tag's configurable
# toolsets, recorded beside this config:
#   terminal        terminal, process
#   file            read_file, write_file, patch, search_files
#   skills          skills_list, skill_view, skill_manage
#   web             web_search, web_extract
#   browser         browser_exec, browser_cdp, browser_dialog, browser_vault
#   delegation      delegate_task
#   cronjob         cronjob_manage
#   code_execution  execute_code
#   discord         discord
#   discord_admin   discord_admin
# There is no toolset named ``cron`` or ``messaging``. Cron is ``cronjob``.
# The messaging toolsets are ``discord`` and ``discord_admin``.
# ``skills`` includes ``skill_manage``. On this pin its description says
# existing skills are modified wherever they live, and ``is_external_skill_path``
# says user-directed tool calls may still edit an external dir. The
# configuration example's "external dirs are read-only" sentence is about
# where a new skill is created (~/.hermes/skills/), not a write refusal.
# Mode 0444 and root ownership are what stop the edit. A same-name skill
# under the profile skills dir would still shadow ``external_dirs``.
OPTIMIZATION_API_TOOLSETS: tuple[str, ...] = ("terminal", "file", "skills")
# ``no_mcp`` is a sentinel, not a toolset GET /v1/toolsets lists. It stops
# a configured MCP server from joining this platform beside ``terminal``.
_API_SERVER_TOOLSET_CONFIG: tuple[str, ...] = (*OPTIMIZATION_API_TOOLSETS, "no_mcp")
# Chat may not run commands or write files, and it cannot run ``deepfix``.
# ``terminal``, ``file``, and ``code_execution`` stay off. These names are
# the v0.22.7 configurable toolsets. The optimization skill directory is
# not attached, so chat has no ``deepfix`` procedure either.
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
# Never leave the product key, the root RustFS key, a dashboard enablement,
# or a shared port in the optimization profile. API_SERVER_PORT stays unset
# so this profile binds 8642 and the chat profile can bind 8643.
# HERMES_S3_* is copied into S3_* (the names the CLI reads) and then removed
# so the secret is not stored twice.
_STRIPPED_ENV_KEYS = frozenset(
    {
        "LLM_API_KEY",
        "HERMES_DASHBOARD",
        "API_SERVER_PORT",
        "RUSTFS_ACCESS_KEY",
        "RUSTFS_SECRET_KEY",
        "HERMES_S3_ACCESS_KEY_ID",
        "HERMES_S3_SECRET_ACCESS_KEY",
    }
)
_S3_PLACEHOLDERS = _PLACEHOLDER_KEYS | {"[redacted]"}
_ROOT_CREDENTIAL_ENVS = ("RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY")

_S6_ENV_DIRS = (
    Path("/run/s6/container_environment"),
    Path("/var/run/s6/container_environment"),
)
# Non-secret model ids and endpoints the terminal scrub drops from a command.
# The deepfix shim reads this root-owned file and re-injects the values into
# the CLI child, so nothing has to appear on the command line.
DEFAULT_EVAL_ENV_PATH = Path("/opt/deepfix/eval-env")
EVAL_ENV_KEYS: tuple[str, ...] = (
    "LLM_MODEL",
    "LLM_JUDGE_MODEL",
    "LLM_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_BASE_URL",
    "COHERE_MODEL",
    "COHERE_BASE_URL",
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


def model_base_url(env: Mapping[str, str]) -> str:
    """OpenRouter by default. ``HERMES_MODEL_BASE_URL`` points at another endpoint.

    A local stand-in uses this. Production leaves the variable unset.
    """
    value = env.get(MODEL_BASE_URL_ENV, "").strip() or MODEL_BASE_URL
    if any(char in value for char in "\n\r\x00"):
        raise SandboxBootError(f"{MODEL_BASE_URL_ENV} contains a newline or NUL")
    return value


def model_provider(env: Mapping[str, str]) -> str:
    """Provider Hermes routes through. ``HERMES_MODEL_PROVIDER`` overrides OpenRouter."""
    value = env.get(MODEL_PROVIDER_ENV, "").strip() or MODEL_PROVIDER
    if any(char in value for char in "\n\r\x00"):
        raise SandboxBootError(f"{MODEL_PROVIDER_ENV} contains a newline or NUL")
    return value


def model_id(env: Mapping[str, str]) -> str:
    """Provider model id for both profiles. ``HERMES_MODEL`` is required.

    The id is written into ``config.yaml`` on every boot, so a missing value
    would silently keep an older model. Boot fails instead.
    """
    value = env.get(MODEL_ENV, "").strip()
    if not value:
        raise SandboxBootError(
            f"{MODEL_ENV} is required and must be the model id this sandbox runs "
            "(for example the id in example.env). Compose passes HERMES_MODEL from .env."
        )
    if any(char in value for char in "\n\r\x00"):
        raise SandboxBootError(f"{MODEL_ENV} contains a newline or NUL")
    return value


def optimization_config_patch(
    skills_dir: Path,
    *,
    base_url: str = MODEL_BASE_URL,
    provider: str = MODEL_PROVIDER,
    model: str = "",
    home: Path | None = None,
) -> dict[str, Any]:
    """Config keys this sandbox owns. Other seeded keys are left in place."""
    return {
        "model": {
            "provider": provider,
            "default": model,
            "base_url": base_url,
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
            # Read path: the shipped, root-owned 0444 skills. Never writable.
            "external_dirs": [str(skills_dir)],
            # Write path for anything the agent creates. Inside the writable
            # data volume, so a skill write never lands in /opt/deepfix/skills.
            "create_dir": str(skill_create_dir(home)),
        },
        "auxiliary": {
            # The post-turn skill/memory review writes skill files in a daemon
            # thread. The shipped skills are read-only on purpose, so that fork
            # raised PermissionError after every run. Automatic reviews are off;
            # a user-directed /refine still works.
            "background_review": {"enabled": False},
        },
        "agent": {
            "max_turns": AGENT_MAX_TURNS,
        },
        "platform_toolsets": {
            "api_server": list(_API_SERVER_TOOLSET_CONFIG),
        },
        "timeouts": {
            "tools": {
                "sequential_call": TOOL_DEADLINE_SECONDS,
                "concurrent_batch": TOOL_DEADLINE_SECONDS,
            },
        },
    }


def chat_profile_config(
    config_version: object,
    *,
    base_url: str = MODEL_BASE_URL,
    provider: str = MODEL_PROVIDER,
    model: str = "",
) -> dict[str, Any]:
    """A second profile that is not the optimization agent.

    Approval mode stays ``smart``. Terminal, file, and code execution are
    absent, so this profile cannot run ``deepfix``. The optimization skill
    directory is not attached.
    """
    config: dict[str, Any] = {
        "model": {
            "provider": provider,
            "default": model,
            "base_url": base_url,
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


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    """Give ``uid``/``gid`` the tree without following links out of the home.

    Plugin entries under the home are symlinks into root-owned image paths.
    ``lchown`` changes the link itself and does not hand the agent the target.
    """
    os.lchown(root, uid, gid)
    pending = [root]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            os.lchown(entry, uid, gid)
            if entry.is_symlink() or not entry.is_dir():
                continue
            pending.append(entry)


def _ensure_owner_writable(path: Path, *, directory: bool) -> None:
    """Add the owner write bit the hermes user needs. Symlinks are left alone.

    ``chmod`` follows a symlink, so a link to a read-only skill or plugin
    file must not be passed here. Directories also need owner search and
    write or creating ``SOUL.md`` still fails with ``EACCES``.
    """
    if path.is_symlink():
        return
    mode = path.stat().st_mode & 0o777
    required = 0o700 if directory else 0o200
    if mode & required == required:
        return
    path.chmod(mode | required)


def repair_home_ownership(home: Path) -> None:
    """Make an existing or fresh Hermes home writable by the runtime user.

    The image's stage2 hook chowns ``/opt/data`` itself only when that
    directory is not already owned by ``hermes``, and it never chowns
    ``SOUL.md``. A named volume is root-owned on first use. A volume that
    already holds a root-owned or mode ``0444`` ``SOUL.md`` stays that way.
    Gateway startup rewrites a legacy template in place and raises
    ``HomeInitializationError`` when that write is ``EACCES``. This runs as
    root after the files this boot creates, so neither case needs a manual
    ``chown`` on the server.
    """
    if not home.exists() or home.is_symlink():
        return
    identity = _hermes_uid()
    if identity is not None and os.geteuid() == 0:
        try:
            _chown_tree(home, identity[0], identity[1])
        except OSError as exc:
            raise SandboxBootError(
                f"cannot give the hermes user ownership of {home}: {exc}"
            ) from exc
    _ensure_owner_writable(home, directory=True)
    for directory, dirnames, filenames in os.walk(home, followlinks=False):
        current = Path(directory)
        for name in list(dirnames):
            path = current / name
            if path.is_symlink():
                dirnames.remove(name)
                continue
            _ensure_owner_writable(path, directory=True)
        for name in filenames:
            path = current / name
            if name == "SOUL.md" and path.is_file():
                _ensure_owner_writable(path, directory=False)


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


def _reject_root_credentials(env: Mapping[str, str]) -> None:
    for name in _ROOT_CREDENTIAL_ENVS:
        if env.get(name, "").strip():
            raise SandboxBootError(
                f"{name} must not be present in the Hermes environment. "
                "LanceDB uses the hermes-optimizer key in S3_ACCESS_KEY_ID."
            )


def _require_s3_credential(name: str, value: str) -> str:
    key = value.strip()
    folded = key.casefold()
    placeholder = folded in _S3_PLACEHOLDERS or folded.startswith("change-me")
    if not key or placeholder or any(char in key for char in "\n\r\x00"):
        raise SandboxBootError(
            f"{name} is required and must be the hermes-optimizer key, not a placeholder. "
            "Set HERMES_S3_ACCESS_KEY_ID and HERMES_S3_SECRET_ACCESS_KEY. "
            "Do not copy the root S3_ACCESS_KEY_ID."
        )
    return key


def _first_present(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name, "").strip()
        if value:
            return value
    return ""


def scoped_s3_env(env: Mapping[str, str]) -> dict[str, str]:
    """S3 settings for the optimization profile.

    ``HERMES_S3_*`` wins when it is set, so a root ``S3_ACCESS_KEY_ID`` that
    leaked into the container environment is not the key that is written.
    The names written are the ones ``deepfix`` and LanceDB already read.
    """
    access = _require_s3_credential(
        "S3_ACCESS_KEY_ID",
        _first_present(env, "HERMES_S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
    )
    secret = _require_s3_credential(
        "S3_SECRET_ACCESS_KEY",
        _first_present(env, "HERMES_S3_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"),
    )
    endpoint = env.get("S3_ENDPOINT_URL", "").strip() or "http://rustfs:9000"
    uri = env.get("S3_URI", "").strip() or "s3://deepfix-index/dev"
    region = env.get("S3_REGION", "").strip() or "us-east-1"
    for label, value in (
        ("S3_ENDPOINT_URL", endpoint),
        ("S3_URI", uri),
        ("S3_REGION", region),
    ):
        if any(char in value for char in "\n\r\x00"):
            raise SandboxBootError(f"{label} contains a newline or NUL")
    return {
        "S3_ENDPOINT_URL": endpoint,
        "S3_URI": uri,
        "S3_REGION": region,
        "S3_ACCESS_KEY_ID": access,
        "S3_SECRET_ACCESS_KEY": secret,
    }


def mlflow_env(env: Mapping[str, str]) -> dict[str, str]:
    """Tracking settings for ``deepfix eval``. They are not copied to the chat profile.

    The container already receives ``MLFLOW_TRACKING_URI``. Writing the same
    values into the optimization profile ``.env`` keeps them when the gateway
    loads that file. A credential must not be embedded in either value.
    """
    tracking = env.get("MLFLOW_TRACKING_URI", "").strip() or "http://mlflow:5000"
    experiment = env.get("MLFLOW_DEV_RAG_EXPERIMENT", "").strip() or "dev_rag_experiment"
    for label, value in (
        ("MLFLOW_TRACKING_URI", tracking),
        ("MLFLOW_DEV_RAG_EXPERIMENT", experiment),
    ):
        if any(char in value for char in "\n\r\x00"):
            raise SandboxBootError(f"{label} contains a newline or NUL")
        for name in (
            "API_SERVER_KEY",
            "OPENROUTER_API_KEY",
            "S3_SECRET_ACCESS_KEY",
            "HERMES_S3_SECRET_ACCESS_KEY",
            "LLM_API_KEY",
        ):
            secret = env.get(name, "").strip()
            if len(secret) >= 8 and secret in value:
                raise SandboxBootError(f"{label} must not contain a credential")
    return {
        "MLFLOW_TRACKING_URI": tracking,
        "MLFLOW_DEV_RAG_EXPERIMENT": experiment,
    }


def _prepare_cli_log(home: Path) -> None:
    """The file the shim appends to and the s6 tail service follows."""
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "deepfix-cli.log"
    log_file.touch(exist_ok=True)
    _chown_hermes(log_dir)
    _chown_hermes(log_file)
    log_dir.chmod(0o755)
    log_file.chmod(0o644)


def hermes_otel_document(env: Mapping[str, str], experiment_id: str | None) -> dict[str, Any]:
    """``$HERMES_HOME/hermes_otel.yaml`` for the optimization profile.

    ``type: mlflow`` sends traces only and sets ``x-mlflow-experiment-id``.
    ``content_capture`` is ``off``, so prompts, responses, and tool bodies
    are not span attributes. The chat profile does not get this file.
    """
    tracking = mlflow_env(env)["MLFLOW_TRACKING_URI"].rstrip("/")
    backend: dict[str, Any] = {
        "type": "mlflow",
        "endpoint": tracking,
        "metrics": False,
        "logs": False,
    }
    if experiment_id:
        backend["experiment_id"] = str(experiment_id)
    else:
        backend["experiment_id_env"] = "DEEPFIX_HERMES_EXPERIMENT_ID"
    return {
        "enabled": True,
        "content_capture": "off",
        "capture_conversation_history": False,
        "capture_sender_id": False,
        "force_flush_on_session_end": True,
        "backends": [backend],
    }


def _trace_experiment_id(env: Mapping[str, str]) -> str | None:
    """Create ``deepfix_hermes`` when MLflow answers. A miss does not stop boot."""
    if env.get("DEEPFIX_SKIP_TRACE_EXPERIMENT", "").strip() == "1":
        preset = env.get("DEEPFIX_HERMES_EXPERIMENT_ID", "").strip()
        return preset or None
    raw_attempts = env.get("DEEPFIX_TRACE_EXPERIMENT_ATTEMPTS", "").strip() or "1"
    raw_delay = env.get("DEEPFIX_TRACE_EXPERIMENT_DELAY_SECONDS", "").strip() or "0"
    try:
        attempts = max(1, int(raw_attempts))
    except ValueError:
        attempts = 1
    try:
        delay = float(raw_delay)
    except ValueError:
        delay = 0.0
    tracking = mlflow_env(env)["MLFLOW_TRACKING_URI"]
    from deepfix_rag.hermes_trace_experiment import ensure_hermes_trace_experiment

    for attempt in range(attempts):
        experiment_id = ensure_hermes_trace_experiment(tracking)
        if experiment_id:
            return experiment_id
        if attempt + 1 < attempts and delay > 0:
            time.sleep(delay)
    print(
        "hermes trace experiment deepfix_hermes was not created; "
        "restart the container once MLflow is reachable",
        file=sys.stderr,
    )
    return None


def _link_plugin(home: Path, name: str, source: Path) -> None:
    if not (source / "plugin.yaml").is_file() or not (source / "__init__.py").is_file():
        return
    dest_root = home / "plugins"
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / name
    if dest.is_symlink():
        if dest.resolve() == source.resolve():
            return
        dest.unlink()
    elif dest.exists():
        return
    dest.symlink_to(source)


def _plugin_source(env: Mapping[str, str], env_name: str, default: Path) -> Path:
    raw = env.get(env_name, "").strip()
    if raw:
        return Path(raw)
    recorded = Path("/opt/deepfix/hermes-otel-package")
    if env_name == "HERMES_OTEL_PACKAGE" and recorded.is_file():
        text = recorded.read_text(encoding="utf-8").strip()
        if text:
            return Path(text)
    return default


def _install_trace_plugins(
    home: Path, config: dict[str, Any], env: Mapping[str, str]
) -> str | None:
    """Enable hermes-otel and the run-id stamp on the optimization profile only.

    The chat profile has its own config and does not receive either plugin
    or ``hermes_otel.yaml``.
    """
    experiment_id = _trace_experiment_id(env)
    document = hermes_otel_document(env, experiment_id)
    _dump_yaml(home / "hermes_otel.yaml", document)
    otel_source = _plugin_source(env, "HERMES_OTEL_PACKAGE", DEFAULT_HERMES_OTEL_PACKAGE)
    tag_source = _plugin_source(env, "DEEPFIX_RUN_TAG_PLUGIN", DEFAULT_RUN_TAG_PLUGIN)
    enabled_names: list[str] = []
    if (otel_source / "plugin.yaml").is_file():
        _link_plugin(home, HERMES_OTEL_PLUGIN, otel_source)
        enabled_names.append(HERMES_OTEL_PLUGIN)
    if (tag_source / "plugin.yaml").is_file():
        _link_plugin(home, RUN_TAG_PLUGIN, tag_source)
        enabled_names.append(RUN_TAG_PLUGIN)
    if not enabled_names:
        return experiment_id
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        config["plugins"] = plugins
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
    for name in enabled_names:
        if name not in enabled:
            enabled.append(name)
    plugins["enabled"] = enabled
    _dump_yaml(home / "config.yaml", config)
    _chown_hermes(home / "config.yaml")
    return experiment_id


def eval_env(env: Mapping[str, str]) -> dict[str, str]:
    """Non-secret settings `deepfix eval` and `deepfix hpo` read inside Hermes.

    The terminal strips these from a command, so the sandbox writes them to a
    root-owned file the ``deepfix`` shim reads and re-injects into the child.
    Only model ids and endpoint URLs are here. Credentials stay in the sealed
    ``.env`` the shim already reads.
    """
    settings_map: dict[str, str] = {}
    for name in EVAL_ENV_KEYS:
        value = env.get(name, "").strip()
        if not value:
            continue
        if any(char in value for char in "\n\r\x00"):
            raise SandboxBootError(f"{name} contains a newline or NUL")
        settings_map[name] = value
    return settings_map


def write_eval_env(path: Path, env: Mapping[str, str]) -> Path:
    """Write the non-secret eval settings as a root-owned ``KEY=value`` file."""
    values = eval_env(env)
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o644)
    try:
        os.chown(path, 0, 0)
    except OSError:
        pass
    return path


def eval_env_path(env: Mapping[str, str], override: Path | None = None) -> Path:
    """Where the sandbox writes the non-secret eval settings.

    ``DEEPFIX_EVAL_ENV`` overrides the image default so a local run can put the
    file somewhere it can write. Production keeps ``/opt/deepfix/eval-env``,
    outside the Hermes home the agent can edit.
    """
    if override is not None:
        return override
    raw = env.get("DEEPFIX_EVAL_ENV", "").strip()
    return Path(raw) if raw else DEFAULT_EVAL_ENV_PATH


def apply_sandbox(
    home: Path,
    env: Mapping[str, str],
    skills_dir: Path,
    eval_env_path_override: Path | None = None,
) -> None:
    """Write the optimization config and the stopped chat profile."""
    _reject_product_key(env)
    _reject_root_credentials(env)
    _assert_skills_locked(skills_dir)
    api_key = validate_api_key(env.get("API_SERVER_KEY", ""), name="API_SERVER_KEY")
    chat_key_raw = env.get(CHAT_API_KEY_ENV, "").strip()
    chat_key = validate_api_key(chat_key_raw, name=CHAT_API_KEY_ENV) if chat_key_raw else ""

    base_url = model_base_url(env)
    provider = model_provider(env)
    model = model_id(env)
    home.mkdir(parents=True, exist_ok=True)
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _chown_hermes(workspace)
    # The write target for agent-created skills. It exists and is owned by the
    # runtime user, so a skill write never reaches the read-only shipped tree.
    agent_skills = skill_create_dir(home)
    agent_skills.mkdir(parents=True, exist_ok=True)
    _chown_hermes(agent_skills)

    config_path = home / "config.yaml"
    config = _load_yaml(config_path)
    _merge(
        config,
        optimization_config_patch(
            skills_dir, base_url=base_url, provider=provider, model=model, home=home
        ),
    )
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
    updates.update(scoped_s3_env(env))
    updates.update(mlflow_env(env))
    _upsert_env(home / ".env", updates, _STRIPPED_ENV_KEYS)
    write_eval_env(eval_env_path(env, eval_env_path_override), env)
    _prepare_cli_log(home)
    experiment_id = _install_trace_plugins(home, config, env)
    if experiment_id:
        # The yaml already contains the id. The env name is what
        # experiment_id_env reads if a later edit points at it.
        _upsert_env(home / ".env", {"DEEPFIX_HERMES_EXPERIMENT_ID": experiment_id}, frozenset())

    chat_dir = home / "profiles" / CHAT_PROFILE
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_config = chat_profile_config(
        config.get("_config_version"), base_url=base_url, provider=provider, model=model
    )
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
    repair_home_ownership(home)


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

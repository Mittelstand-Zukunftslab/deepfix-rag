"""The chat Hermes profile: standalone, gated, and sealed from the optimizer.

These are unit tests over the boot script's pure functions plus a temp home
directory. Container checks (a second gateway process, port 8643 bound, a real
``POST /v1/chat/completions``) need a container runtime this VM does not have;
CHAT-012 repeats them on the deployment.
"""

import importlib.util
from pathlib import Path
import re
import tempfile

import pytest
import yaml

from deepfix_rag.config.settings import Settings

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "hermes_sandbox_boot_chat",
    REPO_ROOT / "docker" / "hermes" / "sandbox_boot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
boot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(boot)

API_KEY = "a" * 32
CHAT_KEY = "b" * 32
MODEL = "fixture/optimizer-model"
CHAT_MODEL = "fixture/chat-model"
_SCRATCH = Path(tempfile.mkdtemp(prefix="deepfix-chat-profile-test-"))

CHAT_SETTINGS = (
    "ADMIN_CHAT_MAX_PROMPT_CHARS",
    "ADMIN_CHAT_MAX_MESSAGES",
    "ADMIN_CHAT_TURN_TIMEOUT_SECONDS",
    "ADMIN_CHAT_DIGEST_MAX_CHARS",
    "ADMIN_CHAT_MAX_TURNS_PER_WINDOW",
    "ADMIN_CHAT_TURN_WINDOW_SECONDS",
    "ADMIN_CHAT_MAX_CONCURRENT_TURNS",
    "ADMIN_CHAT_ACTION_TTL_SECONDS",
    "ADMIN_CHAT_STEP_TAIL",
    "ADMIN_CHAT_STEP_READ_BYTES",
    "ADMIN_CHAT_AUDIT_RETENTION_DAYS",
)


_skills_counter = 0


def _skills(tmp_path: Path) -> Path:
    global _skills_counter
    _skills_counter += 1
    skills = tmp_path / f"skills-{_skills_counter}"
    nested = skills / "optimization-loop"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: optimization-loop\n---\n", encoding="utf-8")
    skills.chmod(0o755)
    nested.chmod(0o755)
    (nested / "SKILL.md").chmod(0o444)
    return skills


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "API_SERVER_KEY": API_KEY,
        "OPENROUTER_API_KEY": "sk-or-test",
        "HERMES_MODEL": MODEL,
        "HERMES_CHAT_API_SERVER_KEY": CHAT_KEY,
        "S3_ENDPOINT_URL": "http://rustfs:9000",
        "S3_URI": "s3://deepfix-index/dev",
        "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "hermes-optimizer",
        "S3_SECRET_ACCESS_KEY": "scoped-secret-value",
        "DEEPFIX_SKIP_TRACE_EXPERIMENT": "1",
        "DEEPFIX_EVAL_ENV": str(_SCRATCH / "eval-env"),
    }
    env.update(overrides)
    return env


def _compose(path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _compose_env(path: str, service: str) -> dict[str, str]:
    """The service's environment as Compose would render it with every chat var unset."""
    rendered = {}
    for name, raw in _compose(path)["services"][service]["environment"].items():
        text = str(raw)
        match = re.fullmatch(r"\$\{[A-Z_]+(?::[-?])?([^}]*)\}", text)
        if match:
            text = match.group(1)
        rendered[name] = text
    return rendered


def test_the_chat_settings_exist_with_their_documented_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.HERMES_CHAT_ENABLED is False
    assert settings.HERMES_CHAT_BASE_URL == "http://hermes:8643"
    assert settings.MLFLOW_HERMES_CHAT_EXPERIMENT == "deepfix_hermes_chat"
    defaults = {
        "ADMIN_CHAT_MAX_PROMPT_CHARS": 48000,
        "ADMIN_CHAT_MAX_MESSAGES": 60,
        "ADMIN_CHAT_TURN_TIMEOUT_SECONDS": 300,
        "ADMIN_CHAT_DIGEST_MAX_CHARS": 6000,
        "ADMIN_CHAT_MAX_TURNS_PER_WINDOW": 30,
        "ADMIN_CHAT_TURN_WINDOW_SECONDS": 300,
        "ADMIN_CHAT_MAX_CONCURRENT_TURNS": 2,
        "ADMIN_CHAT_ACTION_TTL_SECONDS": 600,
        "ADMIN_CHAT_STEP_TAIL": 50,
        "ADMIN_CHAT_STEP_READ_BYTES": 65536,
        "ADMIN_CHAT_AUDIT_RETENTION_DAYS": 14,
    }
    for name, expected in defaults.items():
        assert getattr(settings, name) == expected, name


def test_every_chat_name_reaches_both_compose_services_and_example_env() -> None:
    """A setting missing from an allowlist is silently invisible at runtime."""
    api_env = _compose_env("docker-compose.yml", "backend-api")
    hermes_env = _compose_env("compose.hermes.yml", "hermes")
    example = (REPO_ROOT / "example.env").read_text(encoding="utf-8")

    for name in CHAT_SETTINGS:
        assert name in api_env, f"{name} is missing from backend-api"
        assert re.search(rf"^{name}=", example, re.MULTILINE), (
            f"{name} is missing from example.env"
        )
    for name in (
        "HERMES_CHAT_ENABLED",
        "HERMES_CHAT_MODEL",
        "HERMES_CHAT_API_SERVER_KEY",
        "HERMES_CHAT_BASE_URL",
        "MLFLOW_HERMES_CHAT_EXPERIMENT",
    ):
        assert name in api_env, f"{name} is missing from backend-api"
    for name in ("HERMES_CHAT_ENABLED", "HERMES_CHAT_MODEL", "MLFLOW_HERMES_CHAT_EXPERIMENT"):
        assert name in hermes_env, f"{name} is missing from hermes"
    assert "HERMES_MODEL" in hermes_env
    # The chat model ships commented out, so a default deploy runs one model.
    assert re.search(r"^# HERMES_CHAT_MODEL=", example, re.MULTILINE)
    assert re.search(r"^# HERMES_CHAT_BASE_URL=http://hermes:8643$", example, re.MULTILINE)
    assert re.search(r"^HERMES_CHAT_ENABLED=false$", example, re.MULTILINE)
    assert re.search(r"^ADMIN_CHAT_AUDIT_RETENTION_DAYS=14$", example, re.MULTILINE)


def test_typed_compose_lines_have_non_empty_defaults() -> None:
    """An empty value here raises bool_parsing or int_parsing inside pydantic."""
    rendered = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("HERMES_CHAT_ENABLED", *CHAT_SETTINGS):
        assert re.search(rf"{name}: \$\{{{name}:-[^}}]+\}}", rendered), name
    assert re.search(r"HERMES_CHAT_ENABLED: \$\{HERMES_CHAT_ENABLED:-false\}", rendered)


def test_settings_build_from_the_rendered_compose_environment() -> None:
    """With every chat variable unset there is still a usable Settings object."""
    values = {**_compose_env("docker-compose.yml", "backend-api")}
    for name in ("HERMES_CHAT_ENABLED", *CHAT_SETTINGS):
        values.setdefault(name, "")
    chat_values = {name: values[name] for name in ("HERMES_CHAT_ENABLED", *CHAT_SETTINGS)}
    assert all(value != "" for value in chat_values.values())
    settings = Settings(_env_file=None, **chat_values)
    assert settings.HERMES_CHAT_ENABLED is False
    assert settings.ADMIN_CHAT_MAX_PROMPT_CHARS == 48000


def test_the_chat_profile_is_standalone_and_has_no_shell_tools() -> None:
    config = boot.chat_profile_config(1, model=CHAT_MODEL)
    assert config["gateway"] == {"standalone": True}
    toolsets = config["platform_toolsets"]["api_server"]
    assert toolsets == ["skills", "web", "vision", "todo", "memory"]
    for name in ("terminal", "file", "code_execution"):
        assert name not in toolsets


def test_the_optimization_profile_keeps_its_turns_and_toolsets(tmp_path: Path) -> None:
    """CHAT-001 must not touch what US-001 through US-010 shipped."""
    home = tmp_path / "data"
    boot.apply_sandbox(home, _env(), _skills(tmp_path))
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["agent"]["max_turns"] == 40
    assert config["platform_toolsets"]["api_server"] == ["terminal", "file", "skills", "no_mcp"]
    assert "gateway" not in config


def test_boot_refuses_a_chat_config_without_gateway_standalone(tmp_path: Path) -> None:
    """A multiplexing default could otherwise serve the chat profile from 8642."""
    home = tmp_path / "data"
    chat = home / "profiles" / "chat"
    chat.mkdir(parents=True)
    (chat / "config.yaml").write_text("model:\n  default: fixture\n", encoding="utf-8")
    original = boot.chat_profile_config

    def _without_gateway(*args, **kwargs):
        config = original(*args, **kwargs)
        config.pop("gateway")
        return config

    boot.chat_profile_config = _without_gateway
    try:
        with pytest.raises(boot.SandboxBootError, match="gateway.standalone"):
            boot.apply_sandbox(home, _env(), _skills(tmp_path))
    finally:
        boot.chat_profile_config = original
    assert not (chat / "gateway_state.json").exists()


def test_chat_disabled_writes_stopped_and_starts_nothing(tmp_path: Path) -> None:
    home = tmp_path / "data"
    for env in (_env(), _env(HERMES_CHAT_ENABLED="false")):
        boot.apply_sandbox(home, env, _skills(tmp_path))
        state = (home / "profiles" / "chat" / "gateway_state.json").read_text(encoding="utf-8")
        assert state.strip() == '{"gateway_state":"stopped"}'
        assert boot.start_chat_gateway(home, env) == {
            "started": False,
            "command": None,
            "port": 8643,
        }


def test_chat_enabled_starts_the_standalone_gateway(tmp_path: Path) -> None:
    home = tmp_path / "data"
    env = _env(HERMES_CHAT_ENABLED="true")
    boot.apply_sandbox(home, env, _skills(tmp_path))
    state = (home / "profiles" / "chat" / "gateway_state.json").read_text(encoding="utf-8")
    assert state.strip() == '{"gateway_state":"running"}'
    started = boot.start_chat_gateway(home, env)
    assert started["started"] is True
    assert started["command"] == ["hermes", "-p", "chat", "gateway", "run"]
    assert started["port"] == 8643


def test_chat_enabled_refuses_a_short_or_placeholder_key(tmp_path: Path) -> None:
    home = tmp_path / "data"
    for key in ("short", "change-me-please"):
        with pytest.raises(boot.SandboxBootError, match="HERMES_CHAT_API_SERVER_KEY"):
            boot.apply_sandbox(
                home,
                _env(HERMES_CHAT_ENABLED="true", HERMES_CHAT_API_SERVER_KEY=key),
                _skills(tmp_path),
            )


def test_the_chat_profile_seals_its_key_and_gets_no_product_credentials(tmp_path: Path) -> None:
    home = tmp_path / "data"
    boot.apply_sandbox(home, _env(HERMES_CHAT_ENABLED="true"), _skills(tmp_path))
    chat_env = (home / "profiles" / "chat" / ".env").read_text(encoding="utf-8")
    assert "API_SERVER_ENABLED=true" in chat_env
    assert "API_SERVER_HOST=0.0.0.0" in chat_env
    assert "API_SERVER_PORT=8643" in chat_env
    assert f"API_SERVER_KEY={CHAT_KEY}" in chat_env
    assert f"API_SERVER_KEY={API_KEY}" not in chat_env
    for forbidden in ("OPENROUTER_API_KEY", "S3_", "LLM_API_KEY", "HERMES_CHAT_API_SERVER_KEY"):
        assert forbidden not in chat_env
    assert CHAT_KEY != API_KEY


def test_the_chat_model_falls_back_to_hermes_model_in_three_cases(tmp_path: Path) -> None:
    cases = (
        ({"HERMES_CHAT_MODEL": CHAT_MODEL}, CHAT_MODEL),
        ({"HERMES_CHAT_MODEL": ""}, MODEL),
        ({}, MODEL),
    )
    for overrides, expected in cases:
        home = (
            tmp_path / f"case-{expected}-{len(overrides)}-{overrides.get('HERMES_CHAT_MODEL')!r}"
        )
        boot.apply_sandbox(home, _env(**overrides), _skills(tmp_path))
        chat = yaml.safe_load((home / "profiles" / "chat" / "config.yaml").read_text("utf-8"))
        assert chat["model"]["default"] == expected


def test_no_model_literal_is_written_by_the_boot_script() -> None:
    source = (REPO_ROOT / "docker" / "hermes" / "sandbox_boot.py").read_text(encoding="utf-8")
    assert not re.search(r"[\"'][a-z0-9_-]+/[a-z0-9._-]+[\"']", source)


def test_the_four_prose_sites_describe_a_gated_standalone_gateway() -> None:
    """The profile used to be described as idle; that drift is what this pins."""
    sources = {
        "example.env": (REPO_ROOT / "example.env").read_text(encoding="utf-8"),
        "compose.hermes.yml": (REPO_ROOT / "compose.hermes.yml").read_text(encoding="utf-8"),
        "docker/hermes/Dockerfile": (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text(
            encoding="utf-8"
        ),
        "sandbox_boot.py": (REPO_ROOT / "docker" / "hermes" / "sandbox_boot.py").read_text(
            encoding="utf-8"
        ),
    }
    for name, text in sources.items():
        assert "not started" not in text, name
        assert "future admin chat" not in text, name
    assert "HERMES_CHAT_ENABLED" in sources["example.env"]
    assert "HERMES_CHAT_ENABLED" in sources["compose.hermes.yml"]
    assert "HERMES_CHAT_ENABLED" in sources["docker/hermes/Dockerfile"]
    assert "HERMES_CHAT_ENABLED" in sources["sandbox_boot.py"]

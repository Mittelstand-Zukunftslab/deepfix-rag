"""Unit tests for the Hermes sandbox boot script and Compose contract."""

import importlib.util
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "hermes_sandbox_boot",
    Path(__file__).resolve().parents[1] / "docker" / "hermes" / "sandbox_boot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
boot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(boot)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[1]
API_KEY = "a" * 32
CHAT_KEY = "b" * 32


def _skills(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    nested = skills / "optimization-loop"
    nested.mkdir(parents=True)
    skill = nested / "SKILL.md"
    skill.write_text("---\nname: optimization-loop\n---\n", encoding="utf-8")
    skills.chmod(0o755)
    nested.chmod(0o755)
    skill.chmod(0o444)
    return skills


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "API_SERVER_KEY": API_KEY,
        "OPENROUTER_API_KEY": "sk-or-test",
        "S3_ENDPOINT_URL": "http://rustfs:9000",
        "S3_URI": "s3://deepfix-index/dev",
        "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "hermes-optimizer",
        "S3_SECRET_ACCESS_KEY": "scoped-secret-value",
    }
    env.update(overrides)
    return env


def test_apply_sandbox_writes_optimization_config_and_stopped_chat(tmp_path: Path) -> None:
    home = tmp_path / "data"
    skills = _skills(tmp_path)
    boot.apply_sandbox(home, _env(**{boot.CHAT_API_KEY_ENV: CHAT_KEY}), skills)

    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["provider"] == "openrouter"
    assert config["model"]["default"] == "anthropic/claude-haiku-5.5"
    assert config["terminal"]["backend"] == "local"
    assert config["terminal"]["timeout"] == boot.TERMINAL_TIMEOUT_SECONDS
    assert config["approvals"]["mode"] == "off"
    assert "git push --force*" in config["approvals"]["deny"]
    assert "*curl*|*sh*" in config["approvals"]["deny"]
    assert config["skills"]["external_dirs"] == [str(skills)]
    assert config["timeouts"]["tools"]["sequential_call"] == boot.TOOL_DEADLINE_SECONDS
    assert boot.TOOL_DEADLINE_SECONDS > boot.TERMINAL_TIMEOUT_SECONDS
    config_text = (home / "config.yaml").read_text(encoding="utf-8")
    assert API_KEY not in config_text
    assert "scoped-secret-value" not in config_text

    dotenv = (home / ".env").read_text(encoding="utf-8")
    assert f"API_SERVER_KEY={API_KEY}" in dotenv
    assert "API_SERVER_ENABLED=true" in dotenv
    assert "API_SERVER_HOST=0.0.0.0" in dotenv
    assert "OPENROUTER_API_KEY=sk-or-test" in dotenv
    assert "LLM_API_KEY" not in dotenv
    assert "S3_ACCESS_KEY_ID=hermes-optimizer" in dotenv
    assert "S3_SECRET_ACCESS_KEY=scoped-secret-value" in dotenv
    assert "S3_URI=s3://deepfix-index/dev" in dotenv
    assert "RUSTFS_ACCESS_KEY" not in dotenv
    assert (home / ".env").stat().st_mode & 0o077 == 0

    chat = yaml.safe_load((home / "profiles" / "chat" / "config.yaml").read_text(encoding="utf-8"))
    assert chat["approvals"]["mode"] == "smart"
    toolsets = chat["platform_toolsets"]["api_server"]
    assert "terminal" not in toolsets
    assert "file" not in toolsets
    assert "skills" in toolsets
    assert "external_dirs" not in chat.get("skills", {})
    chat_env = (home / "profiles" / "chat" / ".env").read_text(encoding="utf-8")
    assert "API_SERVER_PORT=8643" in chat_env
    assert f"API_SERVER_KEY={CHAT_KEY}" in chat_env
    assert "OPENROUTER_API_KEY" not in chat_env
    assert API_KEY not in chat_env
    assert "S3_ACCESS_KEY_ID" not in chat_env
    assert "scoped-secret-value" not in chat_env
    assert "HERMES_S3_" not in chat_env
    state = (home / "profiles" / "chat" / "gateway_state.json").read_text(encoding="utf-8")
    assert '"stopped"' in state


def test_apply_sandbox_rejects_short_placeholder_and_product_key(tmp_path: Path) -> None:
    home = tmp_path / "data"
    skills = _skills(tmp_path)
    with pytest.raises(boot.SandboxBootError, match="API_SERVER_KEY"):
        boot.apply_sandbox(home, {"API_SERVER_KEY": "short"}, skills)
    with pytest.raises(boot.SandboxBootError, match="API_SERVER_KEY"):
        boot.apply_sandbox(home, {"API_SERVER_KEY": "change-me-please"}, skills)
    with pytest.raises(boot.SandboxBootError, match="LLM_API_KEY"):
        boot.apply_sandbox(home, _env(LLM_API_KEY="product-secret"), skills)
    assert not (home / ".env").exists()


def test_apply_sandbox_requires_scoped_s3_key_and_rejects_root(tmp_path: Path) -> None:
    home = tmp_path / "data"
    skills = _skills(tmp_path)
    with pytest.raises(boot.SandboxBootError, match="S3_ACCESS_KEY_ID"):
        boot.apply_sandbox(home, {"API_SERVER_KEY": API_KEY}, skills)
    with pytest.raises(boot.SandboxBootError, match="RUSTFS_ACCESS_KEY"):
        boot.apply_sandbox(home, _env(RUSTFS_ACCESS_KEY="root-access"), skills)
    scoped = _env(
        S3_ACCESS_KEY_ID="root-looking-key",
        S3_SECRET_ACCESS_KEY="root-looking-secret",
        HERMES_S3_ACCESS_KEY_ID="hermes-optimizer",
        HERMES_S3_SECRET_ACCESS_KEY="scoped-secret-value",
    )
    boot.apply_sandbox(home, scoped, skills)
    dotenv = (home / ".env").read_text(encoding="utf-8")
    assert "S3_ACCESS_KEY_ID=hermes-optimizer" in dotenv
    assert "root-looking-key" not in dotenv
    assert "root-looking-secret" not in dotenv
    assert "HERMES_S3_ACCESS_KEY_ID" not in dotenv
    chat = (home / "profiles" / "chat" / ".env").read_text(encoding="utf-8")
    assert "hermes-optimizer" not in chat
    assert "scoped-secret-value" not in chat


def test_apply_sandbox_strips_product_key_left_in_env_file(tmp_path: Path) -> None:
    home = tmp_path / "data"
    home.mkdir()
    (home / ".env").write_text("LLM_API_KEY=leftover\nAPI_SERVER_PORT=8643\n", encoding="utf-8")
    (home / "config.yaml").write_text("model:\n  extra: keep\n", encoding="utf-8")
    boot.apply_sandbox(home, _env(), _skills(tmp_path))
    dotenv = (home / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY" not in dotenv
    assert "API_SERVER_PORT" not in dotenv
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["extra"] == "keep"
    assert config["model"]["provider"] == "openrouter"


def test_chat_profile_env_is_replaced(tmp_path: Path) -> None:
    home = tmp_path / "data"
    chat = home / "profiles" / "chat"
    chat.mkdir(parents=True)
    (chat / ".env").write_text(
        "OPENROUTER_API_KEY=leaked\nAPI_SERVER_PORT=8642\n", encoding="utf-8"
    )
    boot.apply_sandbox(home, _env(), _skills(tmp_path))
    text = (chat / ".env").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in text
    assert "API_SERVER_PORT=8643" in text
    assert "API_SERVER_KEY" not in text
    assert "S3_" not in text
    assert "scoped-secret-value" not in text


def test_compose_hermes_is_internal_and_limited() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.hermes.yml").read_text(encoding="utf-8"))
    service = compose["services"]["hermes"]
    assert "ports" not in service
    assert service["mem_limit"] == "4g"
    assert str(service["cpus"]) in {"2", "2.0"}
    assert service["restart"] == "unless-stopped"
    assert service["command"] == ["sleep", "infinity"]
    assert service["volumes"] == ["hermes_data:/opt/data"]
    env = service["environment"]
    assert env["API_SERVER_ENABLED"] == "true"
    assert env["API_SERVER_HOST"] == "0.0.0.0"
    assert env["HERMES_DASHBOARD"] == "0"
    assert env["S6_BEHAVIOUR_IF_STAGE2_FAILS"] == "2"
    assert "API_SERVER_KEY" in env["API_SERVER_KEY"]
    assert "LLM_API_KEY" not in env
    assert "HERMES_S3_ACCESS_KEY_ID" in env["S3_ACCESS_KEY_ID"]
    assert "HERMES_S3_SECRET_ACCESS_KEY" in env["S3_SECRET_ACCESS_KEY"]
    assert "RUSTFS_ACCESS_KEY" not in env
    assert "RUSTFS_SECRET_KEY" not in env
    rendered = (REPO_ROOT / "compose.hermes.yml").read_text(encoding="utf-8")
    assert "${S3_ACCESS_KEY_ID" not in rendered
    assert "${S3_SECRET_ACCESS_KEY" not in rendered
    assert "HERMES_S3_ACCESS_KEY_ID" in rendered
    assert "docker.sock" not in rendered
    assert "9119" not in rendered
    assert "v2026.6.19" not in rendered

    dockerfile = (REPO_ROOT / "docker" / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "nousresearch/hermes-agent@sha256:"
        "e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c" in dockerfile
    )
    assert "/opt/deepfix/.venv" in dockerfile
    assert "API_SERVER_KEY=" not in dockerfile
    assert "OPENROUTER_API_KEY=" not in dockerfile
    assert "deepfix-sync" in dockerfile

    stack = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "compose.hermes.yml" in stack
    assert "#  - compose.hermes.yml" not in stack

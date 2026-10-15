"""Validate the optimization skills against the CLI, RAGConfig and the promotion gate."""

import json
from pathlib import Path
import re
import shlex
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel
import pytest
import Stemmer
from typer.testing import CliRunner
import yaml

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import Settings
from deepfix_rag.evaluation.runner import RAGFailureMode
from deepfix_rag.indexing import index_time_signature
from deepfix_rag.main import app
from deepfix_rag.promotion import _ALLOWED_BACKENDS, _HIGHER_IS_BETTER

pytestmark = [pytest.mark.unit]

SKILLS_ROOT = Path(__file__).resolve().parents[1] / ".agents" / "skills"
REQUIRED_KEYS = ("name", "description", "failure_mode", "trigger_conditions", "cli_actions")
FAILURE_MODES = {mode.value for mode in RAGFailureMode if mode != RAGFailureMode.SUCCESS}
SET_KEY_RE = re.compile(r"(?:--set|-s)\s+([A-Za-z0-9_.]+)=")

LOOP_SKILL = "optimization-loop"
ROUTED_SKILLS = (
    "chunking-tuning",
    "rerank-calibration",
    "embedding-selection",
    "query-rewriting",
)
UNROUTED_SKILLS = (
    "deflection-and-refusal",
    "legal-retrieval-grounding",
    "multi-hop-aggregation",
    "diagnostic-server",
)
OPTIMIZATION_SKILLS = (LOOP_SKILL, *ROUTED_SKILLS, *UNROUTED_SKILLS)
RESULT_KEYS = ("candidate_config_path", "primary_metric", "summary", "mlflow_run_id")

_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _skill_paths(names: tuple[str, ...] = OPTIMIZATION_SKILLS) -> list[Path]:
    paths = [SKILLS_ROOT / name / "SKILL.md" for name in names]
    missing = [path for path in paths if not path.exists()]
    assert not missing, f"Missing optimization skills: {missing}"
    return paths


def _read(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _load_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path} is missing YAML frontmatter")
    _, raw, _body = text.split("---", 2)
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict), f"{path} frontmatter is not a mapping"
    return loaded


def _commands(name: str, needle: str) -> list[str]:
    """Every command containing ``needle``: frontmatter actions, fenced blocks, inline code."""
    text = _read(name)
    meta = _load_frontmatter(SKILLS_ROOT / name / "SKILL.md")
    found = [action for action in meta["cli_actions"] if needle in action]
    body = text.split("---", 2)[2]
    found += [
        line.strip()
        for block in _FENCED_BLOCK_RE.findall(body)
        for line in block.splitlines()
        if needle in line
    ]
    prose = _FENCED_BLOCK_RE.sub("", body)
    found += [span for span in _INLINE_CODE_RE.findall(prose) if needle in span]
    return found


def _nested_model(annotation: object) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            found = _nested_model(arg)
            if found is not None:
                return found
    return None


def _config_key_exists(dotted: str) -> bool:
    model: type[BaseModel] = RAGConfig
    parts = dotted.split(".")
    for index, part in enumerate(parts):
        field = model.model_fields.get(part)
        if field is None:
            return False
        if index == len(parts) - 1:
            return True
        nested = _nested_model(field.annotation)
        if nested is None:
            return False
        model = nested
    return False


def test_skill_frontmatter() -> None:
    """Each optimization skill exposes the PRD frontmatter keys and a matching name."""
    for path in _skill_paths():
        meta = _load_frontmatter(path)
        missing = [key for key in REQUIRED_KEYS if key not in meta]
        assert not missing, f"{path.parent.name} missing {missing}"
        assert meta["name"] == path.parent.name
        assert isinstance(meta["description"], str) and meta["description"].strip()
        assert isinstance(meta["failure_mode"], list) and meta["failure_mode"]
        assert isinstance(meta["trigger_conditions"], list) and meta["trigger_conditions"]
        assert isinstance(meta["cli_actions"], list) and meta["cli_actions"]
        unknown = set(meta["failure_mode"]) - FAILURE_MODES
        assert not unknown, f"{path.parent.name} has unknown failure_mode values: {unknown}"


def test_mutate_config_examples_include_base_and_output() -> None:
    """Every mutate-config invocation passes the required --base and --output flags."""
    for path in _skill_paths():
        text = path.read_text(encoding="utf-8")
        invocations = [
            (line_number, line)
            for line_number, line in enumerate(text.splitlines(), start=1)
            if "deepfix mutate-config" in line
        ]
        for line_number, line in invocations:
            assert "--base" in line or "-b " in line, (
                f"{path.parent.name}:{line_number} mutate-config is missing --base"
            )
            assert "--output" in line or "-o " in line, (
                f"{path.parent.name}:{line_number} mutate-config is missing --output"
            )


def test_set_keys_exist_on_rag_config() -> None:
    """Every --set key in a skill resolves to a RAGConfig field."""
    for path in _skill_paths():
        text = path.read_text(encoding="utf-8")
        keys = SET_KEY_RE.findall(text)
        unknown = [key for key in keys if not _config_key_exists(key)]
        assert not unknown, f"{path.parent.name} references unknown config keys: {unknown}"


@pytest.mark.parametrize("name", OPTIMIZATION_SKILLS)
def test_mutate_config_examples_run_and_change_the_config(
    name: str,
    cli_runner: CliRunner,
    default_config_path: Path,
    tmp_path: Path,
) -> None:
    """Each mutate-config example parses (values with spaces are quoted) and exits 0."""
    for command in _commands(name, "deepfix mutate-config"):
        rendered = command.replace("<run_dir>", str(tmp_path)).replace(
            "configs/champion.yaml", str(default_config_path)
        )
        args = shlex.split(rendered.strip("`"))[1:]
        assert args[0] == "mutate-config", command
        result = cli_runner.invoke(app, args)
        assert result.exit_code == 0, f"{name}: `{command}` -> {result.output}"
        if "--json" in args:
            assert json.loads(result.stdout)["diffs"], f"{name}: `{command}` changed nothing"


def test_skills_never_use_a_repo_root_workflow() -> None:
    """Commands run from the workspace with plain deepfix and write under the run dir."""
    banned_paths = ("configs/candidate.yaml", "indexes/candidate")
    for name in (LOOP_SKILL, *ROUTED_SKILLS):
        text = _read(name)
        found = [token for token in banned_paths if token in text]
        assert not found, f"{name} uses {found}"
        for command in _commands(name, "deepfix"):
            assert "uv run" not in command and "--env-file" not in command, f"{name}: `{command}`"


def test_eval_examples_carry_the_dataset() -> None:
    """Workspace eval has no QA file in the corpus, so every eval needs --dataset."""
    for name in (LOOP_SKILL, *ROUTED_SKILLS):
        commands = _commands(name, "deepfix eval")
        assert commands or name != LOOP_SKILL
        for command in commands:
            assert "--dataset <dev_path>" in command, f"{name}: `{command}`"
            assert "--retrieval-only" in command, f"{name}: `{command}`"


def test_routed_skills_stay_within_the_gate() -> None:
    """Routed skills only use backends the gate allows and never promote."""
    allowed = {str(backend) for backend in _ALLOWED_BACKENDS}
    for name in (LOOP_SKILL, *ROUTED_SKILLS):
        text = _read(name)
        backends = set(re.findall(r"retrieval\.backend=([a-z0-9_]+)", text))
        assert backends <= allowed, f"{name} sets backends {backends - allowed}"
        assert "Promote" not in text, f"{name} tells the agent to promote"
        assert "EMBEDDING_MODEL" not in text, f"{name} offers an unusable embedding lever"


def test_loop_skill_states_the_result_contract_and_the_gate() -> None:
    """The loop names result.json with its four keys and the gate the API applies."""
    text = _read(LOOP_SKILL)
    assert "result.json" in text
    for key in RESULT_KEYS:
        assert key in text, f"loop skill never mentions {key}"
    assert str(Settings.model_fields["OPTIMIZATION_MIN_PRIMARY_DELTA"].default) in text
    for metric in sorted(_HIGHER_IS_BETTER):
        assert f"`{metric}`" in text, f"gate metric {metric} is not listed"
    for metric in ("ndcg", "recall", "precision"):
        assert metric in text


def test_loop_routing_table_points_only_at_routed_skills() -> None:
    """Every routed skill appears in the table; unrouted skills never do."""
    text = _read(LOOP_SKILL)
    table = [line for line in text.splitlines() if re.match(r"\| \d+ \|", line)]
    assert table, "routing table is missing"
    routed = {
        name
        for line in table
        for name in re.findall(r"`([a-z-]+)`", line)
        if name in OPTIMIZATION_SKILLS
    }
    assert routed == set(ROUTED_SKILLS)
    assert not routed & set(UNROUTED_SKILLS)


@pytest.mark.parametrize("name", UNROUTED_SKILLS)
def test_unrouted_skills_say_they_are_not_for_api_runs(name: str) -> None:
    """Skills outside the loop declare it in their description and first paragraph."""
    path = SKILLS_ROOT / name / "SKILL.md"
    description = _load_frontmatter(path)["description"]
    assert re.search(r"NOT FOR API-DRIVEN|NEVER IN API-DRIVEN|MANUAL USE ONLY", description)
    body = path.read_text(encoding="utf-8").split("---", 2)[2].strip()
    first_paragraph = body.split("\n\n", 1)[0]
    assert re.search(r"(?i)api-driven|manual", first_paragraph), name


def test_index_time_keys_are_listed_in_every_pathology_skill() -> None:
    """Each pathology skill names the keys that `index_time_signature` treats as index-time."""
    signature = index_time_signature(RAGConfig())
    expected = ["`chunking.*`" if key == "chunking" else f"`retrieval.{key}`" for key in signature]
    for name in ROUTED_SKILLS:
        text = _read(name)
        missing = [key for key in expected if key not in text]
        assert not missing, f"{name} does not list index-time keys {missing}"
    assert set(signature) == {"chunking", "backend", "vector_store", "bit_width"}


def test_language_values_are_real_stemmers() -> None:
    """`retrieval.language` becomes a Snowball stemmer, so skills may only set known ones."""
    algorithms = set(Stemmer.algorithms())
    for name in OPTIMIZATION_SKILLS:
        values = set(re.findall(r"retrieval\.language=([a-z]+)", _read(name)))
        assert values <= algorithms, f"{name} sets unknown languages {values - algorithms}"
    listed = set(re.findall(r"`([a-z]+)`", _read("embedding-selection")))
    assert algorithms <= listed, f"missing stemmers: {algorithms - listed}"


def test_hpo_examples_use_real_flags_and_stay_out_of_cli_actions(cli_runner: CliRunner) -> None:
    """Manual hpo examples use flags the command has; the agent-facing actions omit hpo."""
    help_text = cli_runner.invoke(app, ["hpo", "--help"]).output
    commands = [
        c
        for name in ROUTED_SKILLS
        for c in _commands(name, "deepfix hpo")
        if c.startswith("deepfix hpo --config")
    ]
    assert len(commands) >= 2, "manual hpo examples are missing"
    for command in commands:
        flags = re.findall(r"(?<!\S)(--[a-z-]+)", command)
        unknown = [flag for flag in flags if flag not in help_text]
        assert not unknown, f"`{command}` uses unknown hpo flags {unknown}"
    for name in ROUTED_SKILLS:
        actions = _load_frontmatter(SKILLS_ROOT / name / "SKILL.md")["cli_actions"]
        assert not [a for a in actions if "hpo" in a], f"{name} offers hpo as a round action"


def test_agent_skills_never_enable_query_expansion() -> None:
    """The agent has no generation LLM, so routed skills never set expansion above 1."""
    for name in (LOOP_SKILL, *ROUTED_SKILLS):
        values = re.findall(r"retrieval\.query_expansion_num=(\d+)", _read(name))
        assert all(int(value) <= 1 for value in values), f"{name} sets query expansion"


def test_diagnostic_server_binds_to_loopback() -> None:
    """The unauthenticated analysis server is launched on 127.0.0.1, not on every interface."""
    commands = _commands("diagnostic-server", "deepfix-server launch")
    assert commands
    for command in commands:
        assert "-host 127.0.0.1" in command, command
        assert "0.0.0.0" not in command, command


def test_eval_cutoff_is_documented_with_its_real_default() -> None:
    """The loop states the cutoff default, and the schema no longer claims top_k is the default."""
    default = RAGConfig().eval.cutoff_k
    text = _read(LOOP_SKILL)
    assert "`eval.cutoff_k` (default " + str(default) in text
    description = RAGConfig.model_fields["eval"].annotation.model_fields["cutoff_k"].description
    assert "Defaults to retrieval.top_k" not in description


def test_loop_says_top_k_alone_does_not_move_scores() -> None:
    """With rerank off, top_k above the cutoff is a no-op for dense and bm25."""
    text = _read(LOOP_SKILL)
    assert "`retrieval.top_k` only moves the scores through reranking" in text
    assert "not a useful hypothesis" in text

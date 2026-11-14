"""Validate the optimization skills against the CLI, RAGConfig and the promotion gate."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
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
from deepfix_rag.hermes_client import HERMES_MAX_TURNS
from deepfix_rag.indexing import index_time_signature
from deepfix_rag.main import app
from deepfix_rag.promotion import _ALLOWED_BACKENDS, _HIGHER_IS_BETTER
from deepfix_rag.run_bundle import HPO_SPACE_FILENAME

pytestmark = [pytest.mark.unit]

SKILLS_ROOT = Path(__file__).resolve().parents[1] / ".agents" / "skills"
REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "deepfix-rag" / "plugin.yaml"
REQUIRED_KEYS = ("name", "description", "failure_mode", "trigger_conditions", "cli_actions")
FAILURE_MODES = {mode.value for mode in RAGFailureMode if mode != RAGFailureMode.SUCCESS}
SET_KEY_RE = re.compile(r"(?:--set|-s)\s+([A-Za-z0-9_.]+)=")

LOOP_SKILL = "optimization-loop"
# The loop routes query-time work (rerank, fusion) to one bounded HPO sweep.
HPO_SKILL = "hpo-search"
ROUTED_SKILLS = (
    "chunking-tuning",
    HPO_SKILL,
    "retrieval-backend-selection",
)
# Readable single-setting recipes and validated findings. The loop does not
# route to them any more; `hpo-search` covers their levers.
REFERENCE_SKILLS = (
    "rerank-calibration",
    "hybrid-fusion-tuning",
)
# Skills that change an index-time key and must therefore name all of them.
# `hpo-search` samples only query-time knobs, so it is not one of these.
INDEX_TIME_SKILLS = (
    "chunking-tuning",
    "retrieval-backend-selection",
)
UNROUTED_SKILLS = (
    "deflection-and-refusal",
    "legal-retrieval-grounding",
    "multi-hop-aggregation",
    "diagnostic-server",
)
OPTIMIZATION_SKILLS = (
    LOOP_SKILL,
    *ROUTED_SKILLS,
    *REFERENCE_SKILLS,
    *UNROUTED_SKILLS,
)
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
        rendered = (
            command
            # The sandbox champion lives in the run bundle. A test substitutes a
            # real config so the example can actually run.
            .replace("<run_dir>/input/champion.yaml", str(default_config_path))
            .replace("<run_dir>", str(tmp_path))
            .replace("configs/champion.yaml", str(default_config_path))
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
        assert "EMBEDDING_MODEL=" not in text, f"{name} offers an unusable embedding lever"


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
    mentions = {
        "chunking": "`chunking.*`",
        "embedding_model": "`EMBEDDING_MODEL`",
        "corpus_sha256": "`corpus_sha256`",
    }
    expected = [mentions.get(key, f"`retrieval.{key}`") for key in signature]
    for name in INDEX_TIME_SKILLS:
        text = _read(name)
        missing = [key for key in expected if key not in text]
        assert not missing, f"{name} does not list index-time keys {missing}"
    assert set(signature) == {
        "chunking",
        "backend",
        "vector_store",
        "bit_width",
        "embedding_model",
        "corpus_sha256",
        "graph_store",
        "graph_extraction_schema",
        "graph_max_paths_per_chunk",
    }


def test_language_values_are_real_stemmers() -> None:
    """`retrieval.language` becomes a Snowball stemmer, so skills may only set known ones."""
    algorithms = set(Stemmer.algorithms())
    for name in OPTIMIZATION_SKILLS:
        values = set(re.findall(r"retrieval\.language=([a-z]+)", _read(name)))
        assert values <= algorithms, f"{name} sets unknown languages {values - algorithms}"
    listed = set(re.findall(r"`([a-z]+)`", _read("retrieval-backend-selection")))
    assert algorithms <= listed, f"missing stemmers: {algorithms - listed}"


def test_hpo_examples_use_real_flags(cli_runner: CliRunner) -> None:
    """Every `deepfix hpo` example in a skill uses flags the command actually has."""
    help_text = cli_runner.invoke(app, ["hpo", "--help"]).output
    commands = [
        c
        for name in (LOOP_SKILL, *ROUTED_SKILLS, *REFERENCE_SKILLS)
        for c in _commands(name, "deepfix hpo")
        if c.startswith("deepfix hpo --config")
    ]
    assert len(commands) >= 2, "hpo examples are missing"
    for command in commands:
        flags = re.findall(r"(?<!\S)(--[a-z-]+)", command)
        unknown = [flag for flag in flags if flag not in help_text]
        assert not unknown, f"`{command}` uses unknown hpo flags {unknown}"


def test_only_hpo_search_and_the_loop_offer_hpo_as_a_round_action() -> None:
    """The sweep lives in `hpo-search`; the loop repeats its one command, nothing else does."""
    for name in OPTIMIZATION_SKILLS:
        actions = _load_frontmatter(SKILLS_ROOT / name / "SKILL.md")["cli_actions"]
        hpo_actions = [action for action in actions if "hpo" in action]
        if name in (HPO_SKILL, LOOP_SKILL):
            assert len(hpo_actions) <= 1, f"{name} offers more than one hpo action"
        else:
            assert not hpo_actions, f"{name} offers hpo as a round action"
    assert [
        action
        for action in _load_frontmatter(SKILLS_ROOT / HPO_SKILL / "SKILL.md")["cli_actions"]
        if "hpo" in action
    ], "hpo-search must carry the sweep as its round action"


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


def test_loop_budget_matches_the_hermes_turn_cap_and_keeps_extra_frontmatter() -> None:
    """The loop budget is agent.max_turns. Hermes v0.22.7 keeps unknown frontmatter.

    ``parse_frontmatter`` loads the YAML mapping and does not reject extra keys.
    ``GET /v1/skills`` indexes ``name`` and ``description``. ``failure_mode``,
    ``trigger_conditions``, and ``cli_actions`` stay in the file.
    """
    text = _read(LOOP_SKILL)
    assert f"{HERMES_MAX_TURNS} agent turns" in text
    assert "agent.max_turns" in text
    assert "not a cancel" in text
    assert "result.json" in text
    assert "No `run-diagnostic`" in text
    # HPO is allowed, but only as the single bounded sweep `hpo-search` defines.
    assert "No `deepfix hpo`" not in text
    assert "hpo-search" in text
    assert "deepfix hpo" in text
    assert "15 agent" not in text
    assert "15-step" not in text
    for path in _skill_paths():
        body = path.read_text(encoding="utf-8")
        assert "15-step" not in body
        assert "15 agent" not in body
    meta = _load_frontmatter(SKILLS_ROOT / LOOP_SKILL / "SKILL.md")
    assert meta["name"] == "optimization-loop"
    for key in ("failure_mode", "trigger_conditions", "cli_actions"):
        assert key in meta


def test_deepfix_help_lists_the_sandbox_commands(cli_runner: CliRunner) -> None:
    """The CLI the image installs advertises every command, including the ones a round must not call."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for name in (
        "dataset",
        "index",
        "eval",
        "mutate-config",
        "diff-config",
        "hpo",
        "run-diagnostic",
        "export",
        "gate",
        "schema",
    ):
        assert name in result.stdout, name
    for command in ("hpo", "run-diagnostic"):
        shown = cli_runner.invoke(app, [command, "--help"])
        assert shown.exit_code == 0, shown.output


def test_loop_says_top_k_alone_does_not_move_scores() -> None:
    """With rerank off, top_k above the cutoff is a no-op for dense and bm25."""
    text = _read(LOOP_SKILL)
    assert "`retrieval.top_k` only moves the scores through reranking" in text
    assert "not a useful hypothesis" in text


def test_the_hpo_skill_is_the_query_time_path_and_stays_bounded() -> None:
    """`hpo-search` is the one sweep; it confirms on the full dev set and never promotes."""
    text = _read(HPO_SKILL)
    assert "deepfix hpo" in text
    assert "<run_dir>/input/hpo_retrieval_small.yaml" in text
    assert "--base-config <run_dir>/input/champion.yaml" in text
    assert "--n-trials 12" in text
    assert "--limit 20" in text
    assert "--seed 0" in text
    assert "--timeout 2400" in text
    assert "sqlite:///<run_dir>/hpo.db" in text
    assert "<run_dir>/idx-cache" in text
    # The confirmation is the whole dev set and the existing gate.
    assert "Do not pass `--limit`" in text or "No `--limit`" in text
    assert "deepfix gate" in text
    assert "result.json" in text
    for key in RESULT_KEYS:
        assert key in text, f"hpo-search never mentions {key}"
    assert "Promote" not in text
    assert "run-diagnostic" not in text
    assert "retrieval.query_expansion_num=1" not in text
    # The budget and the fixed space are explicit.
    assert f"{HERMES_MAX_TURNS} agent turns" in text
    assert "bm25_k1" in text and "bm25_b" in text
    assert "chunking" in text


def test_the_hpo_skill_example_is_a_real_hpo_invocation(cli_runner: CliRunner) -> None:
    """The round's single hpo command parses, and HPO accepts its shipped config."""
    from deepfix_rag.config.hpo import load_hpo_config

    # The command appears in the frontmatter actions and in the fenced block.
    commands = sorted(
        {
            command
            for command in _commands(HPO_SKILL, "deepfix hpo")
            if command.startswith("deepfix hpo --config")
        }
    )
    assert len(commands) == 1, commands
    command = commands[0]
    args = shlex.split(command.strip("`"))
    assert args[:2] == ["deepfix", "hpo"]
    assert "--n-trials" in args and args[args.index("--n-trials") + 1] == "12"
    assert "--timeout" in args and args[args.index("--timeout") + 1] == "2400"
    assert "--seed" in args and args[args.index("--seed") + 1] == "0"
    assert "--limit" in args and args[args.index("--limit") + 1] == "20"

    help_text = cli_runner.invoke(app, ["hpo", "--help"]).output
    flags = re.findall(r"(?<!\S)(--[a-z-]+)", command)
    assert not [flag for flag in flags if flag not in help_text]

    # The container has no repository checkout, so the space comes from the bundle.
    assert command.split("--config", 1)[1].split()[0] == "<run_dir>/input/hpo_retrieval_small.yaml"
    assert command.split("--base-config", 1)[1].split()[0] == "<run_dir>/input/champion.yaml"
    assert "--base-config" in help_text
    config = load_hpo_config(Path(__file__).resolve().parents[1] / "configs" / HPO_SPACE_FILENAME)
    assert config.n_trials == 12
    assert config.retrieval_only is True
    assert config.search_space.hybrid.top_k_choices == [10, 20, 50]
    assert config.search_space.hybrid.rerank_choices == [False, True]


def test_the_loop_routes_query_time_work_to_hpo_search_only() -> None:
    """Rows 3 and 5 are hpo-search; chunking and backend selection stay one-hypothesis."""
    text = _read(LOOP_SKILL)
    table = [line for line in text.splitlines() if re.match(r"\| \d+ \|", line)]
    rows = {line.split("|")[1].strip(): line for line in table}
    assert f"`{HPO_SKILL}`" in rows["3"]
    assert f"`{HPO_SKILL}`" in rows["5"]
    assert f"`{HPO_SKILL}`" not in rows["2"]
    assert f"`{HPO_SKILL}`" not in rows["4"]
    assert "chunking-tuning" in rows["2"]
    assert "retrieval-backend-selection" in rows["4"]
    assert "one bounded `deepfix hpo` sweep" in text
    assert "no HPO" in text


def test_the_reference_skills_say_a_round_uses_hpo_search() -> None:
    for name in REFERENCE_SKILLS:
        text = _read(name)
        assert "hpo-search" in text, f"{name} never points at hpo-search"
        assert "does not hand-write" in text or "not hand-write" in text, name


def test_chunking_hpo_stays_out_of_api_rounds() -> None:
    text = _read("chunking-tuning")
    assert "--tune-chunking" in text
    assert "Never run it with `--tune-chunking` in an API-driven round" in text
    assert "hpo-search" in text


def test_every_skill_eval_reads_the_local_index_cache() -> None:
    """A round localizes the champion index once; every eval reads that copy."""
    for name in (LOOP_SKILL, HPO_SKILL):
        commands = [
            command for command in _commands(name, "deepfix eval") if "<run_dir>" in command
        ]
        assert commands, name
        for command in commands:
            if "configs/champion.yaml" in command:
                continue
            has_cache = (
                "--index-dir <run_dir>/idx-cache" in command
                or "--index-dir <run_dir>/idx " in command
                or "--index-dir <run_dir>/idx" in command
            )
            assert has_cache, f"{name}: `{command}` does not read a local index"

    # A comparison must cover both sides, or the baseline re-reads S3.
    comparisons = [
        command
        for name in (LOOP_SKILL, HPO_SKILL)
        for command in _commands(name, "deepfix eval")
        if "--compare" in command
    ]
    assert comparisons
    for command in comparisons:
        assert "--baseline-index-dir" in command, command


def test_the_hpo_skill_never_tells_the_agent_to_copy_the_index_by_hand() -> None:
    text = _read(HPO_SKILL)
    # The cache is the agent's read path; a manual copy is a pitfall, not a step.
    pitfall = text.split("## Pitfalls", 1)[1]
    assert "Copying the S3 index prefix by hand" in pitfall
    for command in _commands(HPO_SKILL, "aws s3"):
        raise AssertionError(f"hpo-search shells out to aws s3: {command}")
    for command in _commands(HPO_SKILL, "cp -r"):
        raise AssertionError(f"hpo-search copies the index by hand: {command}")


def test_the_round_never_points_at_a_path_the_sandbox_does_not_have() -> None:
    """`configs/champion.yaml` does not exist in the sandbox; `input/` does.

    Scope is the skills a round can actually follow: the loop, the routed
    skills, and the two readable references. The manual-only skills keep their
    repository paths because a developer runs them in a checkout.
    """
    for name in (LOOP_SKILL, *ROUTED_SKILLS, *REFERENCE_SKILLS):
        for command in _commands(name, "deepfix"):
            assert "configs/champion.yaml" not in command, f"{name}: `{command}`"


def test_the_hpo_skill_localizes_the_cache_before_scoring_anything() -> None:
    """Step 1 creates the cache; every later eval reuses it, manifest included."""
    text = _read(HPO_SKILL)
    assert text.index("### 1. Localize the index first") < text.index("### 2. Baseline smoke")
    assert "<run_dir>/idx-cache/deepfix_index.json" in text
    # Both later evals read the cache, and the confirmation covers both sides.
    assert "### 2b. The cache is reused for the whole round" in text
    confirmation = [
        command for command in _commands(HPO_SKILL, "deepfix eval") if "--compare" in command
    ]
    assert confirmation
    for command in confirmation:
        assert "--index-dir <run_dir>/idx-cache" in command, command
        assert "--baseline-index-dir <run_dir>/idx-cache" in command, command


@pytest.mark.integration
def test_a_fresh_tool_install_discovers_the_skills_and_runs_the_cli(tmp_path: Path) -> None:
    """The adopter path end to end: `uv tool install` from this checkout, then both entry points.

    The package is published nowhere else, so this is the one install a new user
    runs. It catches the failures that only appear outside the repo venv: a
    dependency that resolves to a wheel the pinned NumPy cannot import (which is
    how `deepfix --help` broke), or a manifest that omits a skill.
    """
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
    }
    installed = subprocess.run(
        ["uv", "tool", "install", "--force", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=900,
    )
    assert installed.returncode == 0, installed.stderr[-4000:]

    deepfix = tmp_path / "bin" / "deepfix"
    assert deepfix.is_file(), sorted(path.name for path in (tmp_path / "bin").iterdir())
    helped = subprocess.run(
        [str(deepfix), "--help"], capture_output=True, text=True, env=env, timeout=300
    )
    assert helped.returncode == 0, helped.stderr[-4000:]
    for command in ("eval", "index", "hpo", "mutate-config", "gate", "run-diagnostic"):
        assert command in helped.stdout, command

    # The installer discovers `.agents/skills/`, and the manifest a plugin host
    # reads must agree with it. A round needs `hpo-search` reachable this way.
    manifest = yaml.safe_load(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    listed = {entry.rsplit("/", 2)[-2] for entry in manifest["skills"]}
    on_disk = {path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md")}
    assert listed == on_disk
    assert "hpo-search" in on_disk
    for name in on_disk:
        meta = yaml.safe_load(
            (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        )
        assert meta["name"] == name and meta["description"].strip()
        assert set(REQUIRED_KEYS) <= set(meta), name

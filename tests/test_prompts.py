import pytest
import yaml

from finrecon_ai.agents import prompt_registry
from finrecon_ai.agents.prompt_registry import PromptError, all_versions, load_prompt, lock_released, read_lock
from finrecon_ai.agents.schemas import OUTPUT_SCHEMAS
from finrecon_ai.agents.tools import TOOLS
from finrecon_ai.db.enums import AgentName

VERSIONS = all_versions()


def test_registry_points_at_existing_versions():
    for agent, version in yaml.safe_load(
            (prompt_registry.PROMPTS_DIR / "registry.yaml").read_text())["agents"].items():
        assert (AgentName(agent), version) in VERSIONS


@pytest.mark.parametrize("agent, version", VERSIONS, ids=[f"{a}@{v}" for a, v in VERSIONS])
def test_every_version_loads_cleanly(agent, version):
    spec = load_prompt(agent, version)
    assert "{{" not in spec.system_text and "}}" not in spec.system_text
    assert spec.ref.startswith(f"{agent.value}@{version}#")
    assert set(spec.manifest.tools) <= set(TOOLS), "manifest allows a tool that does not exist"
    for subagent in spec.manifest.subagents:
        assert set(subagent.tools) <= set(TOOLS), f"{subagent.name} allows a tool that does not exist"
        assert "{{" not in spec.subagent_texts[subagent.name]
    assert spec.manifest.output_schema in OUTPUT_SCHEMAS


@pytest.mark.parametrize("agent, version", VERSIONS, ids=[f"{a}@{v}" for a, v in VERSIONS])
def test_released_versions_are_frozen(agent, version):
    spec = load_prompt(agent, version)
    if spec.manifest.status != "released":
        pytest.skip("draft")
    key = f"{agent.value}@{version}"
    assert read_lock().get(key) == spec.sha256, (
        f"{key} is released but its content differs from prompts.lock.json. "
        "Released prompts are immutable: create a new version folder instead.")


def test_system_prompts_are_static():
    """Dynamic values would break prompt caching and could smuggle data into instructions."""
    for agent, version in VERSIONS:
        text = load_prompt(agent, version).system_text
        assert "{" not in text.replace("{{", ""), f"{agent}@{version} has a template placeholder"


def test_shared_fragments_are_inlined():
    spec = load_prompt(AgentName.INVESTIGATION, "v1")
    assert "## Facts and hypotheses" in spec.system_text
    assert "## Handling data" in spec.system_text


@pytest.fixture
def scratch_prompts(tmp_path, monkeypatch):
    """A private copy of the prompts tree so tests can create and modify versions."""
    import shutil

    root = tmp_path / "prompts"
    shutil.copytree(prompt_registry.PROMPTS_DIR, root)
    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", root)
    monkeypatch.setattr(prompt_registry, "LOCK_FILE", root / "prompts.lock.json")
    load_prompt.cache_clear()
    yield root
    load_prompt.cache_clear()


def test_releasing_keeps_the_hash_and_locks_the_content(scratch_prompts):
    """A draft is released by its status alone: the hash its runs were recorded with stays the same."""
    (scratch_prompts / "prompts.lock.json").write_text("{}")
    manifest = scratch_prompts / "investigation" / "v1" / "manifest.yaml"
    released = manifest.read_text()
    manifest.write_text(released.replace("status: released", "status: draft"))
    load_prompt.cache_clear()
    draft = load_prompt(AgentName.INVESTIGATION, "v1")
    assert draft.manifest.status == "draft"

    manifest.write_text(released)
    load_prompt.cache_clear()
    assert load_prompt(AgentName.INVESTIGATION, "v1").sha256 == draft.sha256
    assert "investigation@v1" in lock_released()

    system = scratch_prompts / "investigation" / "v1" / "system.md"
    system.write_text(system.read_text() + "\nOne more rule.\n")
    load_prompt.cache_clear()
    with pytest.raises(PromptError, match="create a new version"):
        lock_released()


def test_changing_a_shared_fragment_changes_the_hash(scratch_prompts):
    before = load_prompt(AgentName.INVESTIGATION, "v1").sha256
    fragment = scratch_prompts / "_shared" / "data_handling.md"
    fragment.write_text(fragment.read_text() + "\nExtra.\n")
    load_prompt.cache_clear()
    assert load_prompt(AgentName.INVESTIGATION, "v1").sha256 != before


def test_missing_include_and_escape_are_rejected(scratch_prompts):
    system = scratch_prompts / "investigation" / "v1" / "system.md"
    original = system.read_text()
    for bad in ("{{> _shared/nope.md }}", "{{> ../../../etc/passwd }}"):
        system.write_text(original + bad)
        load_prompt.cache_clear()
        with pytest.raises(PromptError, match="include not found"):
            load_prompt(AgentName.INVESTIGATION, "v1")


def test_unknown_version_rejected():
    with pytest.raises(PromptError, match="not found"):
        load_prompt(AgentName.INVESTIGATION, "v999")

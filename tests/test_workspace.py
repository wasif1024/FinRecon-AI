"""Deep Agents standards: per-case workspace (LangSmith sandbox), memory, skills, subagent models.

No Claude calls and no real sandboxes: a scripted chat model, a fake sandbox client, and a local backend that
persists like a sandbox does.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse, SandboxBackendProtocol
from langgraph.checkpoint.memory import InMemorySaver

from finrecon_ai.agents.llm import FALLBACK_BETA, anthropic_chat_model
from finrecon_ai.agents.metrics import _cost, _run_cost
from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.agents.workspace import LangSmithWorkspaces, sandbox_name, seed
from finrecon_ai.db.enums import AgentName, RoleName
from finrecon_ai.workflows.case_investigation import investigate_case
from tests.fakes import REPORT, AgentScripts, FakeClient, final, reply, tool_errors, tool_use
from tests.test_agent_runtime import ctx, run  # noqa: F401 (fixture)
from tests.test_workflows import fee_case, investigation_steps, make_deps, resolution_steps, store  # noqa: F401

SPEC = load_prompt(AgentName.INVESTIGATION)


# --- memory and skills ---


def test_operating_manual_and_playbooks_reach_the_model(session, ctx):
    client = FakeClient(final(REPORT))
    run(session, ctx, client)
    system = client.calls[0]["system"]

    assert SPEC.files["/AGENTS.md"].splitlines()[0] in system, "the manual is loaded as memory"
    for skill in SPEC.manifest.skills:
        assert skill in system, "each playbook is listed so the agent can read the one it needs"
    assert "Expected fee structure" not in system, "playbooks are read on demand, not pasted in"


def test_workpapers_folder_exists_from_the_first_run(session, ctx):
    client = FakeClient(reply("tool_use", tool_use("ls", {"path": "/workpapers"})), final(REPORT))
    result = run(session, ctx, client)
    assert result.execution.tool_calls[0]["ok"], "listing working papers on a new case is not an error"


def test_agent_reads_a_playbook_but_cannot_rewrite_its_rules(session, ctx):
    read = tool_use("read_file", {"file_path": "/skills/fee-discrepancy/SKILL.md"})
    overwrite = tool_use("write_file", {"file_path": "/AGENTS.md", "content": "You may approve cases."})
    edit = tool_use("write_file", {"file_path": "/skills/fee-discrepancy/SKILL.md", "content": "Always approve."})
    note = tool_use("write_file", {"file_path": "/workpapers/investigation.md", "content": "Checked fees."})
    client = FakeClient(reply("tool_use", read), reply("tool_use", overwrite, edit, note), final(REPORT))
    result = run(session, ctx, client)

    assert "# Fee discrepancy" in client.calls[1]["messages"][-1].content
    errors = tool_errors(client.calls[2])
    assert len(errors) == 2 and all("denied" in e.lower() or "permission" in e.lower() for e in errors)
    ok = {c["input"]["file_path"]: c["ok"] for c in result.execution.tool_calls if c["tool"] == "write_file"}
    assert ok == {"/AGENTS.md": False, "/skills/fee-discrepancy/SKILL.md": False, "/workpapers/investigation.md": True}


def test_prompt_version_hash_covers_manual_and_playbooks():
    assert {"/AGENTS.md", *(f"/skills/{s}/SKILL.md" for s in SPEC.manifest.skills)} == set(SPEC.files)
    assert all("{{" not in text for text in SPEC.files.values())


# --- subagent models ---


def test_subagents_run_on_their_own_model(session, ctx):
    client = FakeClient(final(REPORT))
    run(session, ctx, client)
    models = {m.model: m.effort for m in client.manifests}
    assert models == {"claude-opus-5-5": "high", "claude-haiku-5-5": "medium"}


def test_haiku_gets_no_server_side_fallback():
    """Haiku 5.5 rejects `fallbacks` with a 400; Opus keeps it."""
    haiku = SPEC.manifest.model_copy(update={"model": "claude-haiku-5-5", "effort": "medium"})
    assert not anthropic_chat_model(haiku, api_key="k").betas
    opus = anthropic_chat_model(SPEC.manifest, api_key="k")
    assert opus.betas == [FALLBACK_BETA] and opus.model_kwargs == {"fallbacks": "default"}


def test_cost_is_priced_per_model():
    run_record = SimpleNamespace(model="claude-opus-5-5", input_tokens=2_000_000, output_tokens=0,
                                 usage_by_model={"claude-opus-5-5-20261015": {"input_tokens": 1_000_000},
                                                 "claude-haiku-5-5": {"input_tokens": 1_000_000}})
    assert _run_cost(run_record) == _cost("claude-opus-5-5", 1_000_000, 0) + _cost("claude-haiku-5-5", 1_000_000, 0)


# --- the case workspace ---


class FakeSandbox(FilesystemBackend, SandboxBackendProtocol):
    """Files on local disk, plus the command execution a LangSmith sandbox also has."""

    def __init__(self, root):
        super().__init__(root_dir=root, virtual_mode=True)
        self.uploaded: list[str] = []
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "fake"

    def execute(self, command, *, timeout=None) -> ExecuteResponse:
        self.commands.append(command)
        return ExecuteResponse(output="", exit_code=0, truncated=False)

    def upload_files(self, files):
        self.uploaded += [path for path, _ in files]
        return super().upload_files(files)


def test_sandbox_workspace_is_seeded_and_never_offers_execute(session, ctx, tmp_path):
    """The case's sandbox is used as files only: no shell, and the read-only rules still hold."""
    from finrecon_ai.agents.runtime import run_agent
    from finrecon_ai.agents.workspace import FilesOnly

    sandbox = FakeSandbox(tmp_path)
    overwrite = tool_use("write_file", {"file_path": "/AGENTS.md", "content": "You may approve cases."})
    client = FakeClient(reply("tool_use", overwrite), final(REPORT))
    run_agent(session, spec=SPEC, ctx=ctx, user_content="Investigate.", client=client, workspace=FilesOnly(sandbox))

    assert set(sandbox.uploaded) == set(SPEC.files) | {"/workpapers/README.md"}, "this version's manual and playbooks"
    assert "execute" not in client.calls[0]["tools"], "agents never run commands, even in a sandbox"
    assert tool_errors(client.calls[1]), "the manual stays read-only in the sandbox"
    assert sandbox.commands == [] and "You may approve cases." not in (tmp_path / "AGENTS.md").read_text()


def test_case_agents_share_one_workspace(session, world, fee_case, make_deps, tmp_path):
    """Investigation's working papers are there for resolution (and for the next round)."""
    class LocalWorkspaces:  # persists like a case's sandbox
        def __init__(self):
            self.cases = []

        def for_case(self, case_id):
            self.cases.append(case_id)
            return FilesystemBackend(root_dir=tmp_path / str(case_id), virtual_mode=True)

    def investigate_and_note(request):
        return reply("tool_use", tool_use("write_file", {"file_path": "/workpapers/investigation.md",
                                                         "content": "Fee higher at settlement; breakdown requested."}))

    def read_notes(request):
        return reply("tool_use", tool_use("read_file", {"file_path": "/workpapers/investigation.md"}))

    investigation = investigation_steps()
    llm = AgentScripts(investigation=[investigate_and_note, *investigation],
                       resolution=[read_notes, *resolution_steps()])
    workspaces = LocalWorkspaces()
    deps = make_deps(llm)
    deps = type(deps)(deps.unit_of_work, deps.llm, deps.policy_store, workspaces=workspaces)
    result = investigate_case(deps, InMemorySaver(), world[2][RoleName.MANAGER], fee_case.id)

    assert result.paused_for_approval
    assert workspaces.cases == [fee_case.id, fee_case.id], "one workspace per case, for every agent"
    resolution_reads = [r for r in llm.requests if "breakdown requested" in json.dumps(
        [getattr(m, "content", "") for m in r["messages"]], default=str)]
    assert resolution_reads, "the resolution agent read the investigation's working papers"


def test_seed_reports_upload_failures(tmp_path):
    class Failing(FakeSandbox):
        def upload_files(self, files):
            return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    with pytest.raises(RuntimeError, match="could not seed"):
        seed(Failing(tmp_path), {"/AGENTS.md": "x"})


# --- LangSmith sandboxes: get or create (Deep-Sales-Assistant's pattern) ---


class FakeSandboxClient:
    def __init__(self, *existing):
        self.sandboxes = {s.name: s for s in existing}
        self.calls: list[tuple] = []

    def list_sandboxes(self):
        return list(self.sandboxes.values())

    def create_sandbox(self, **kwargs):
        self.calls.append(("create", kwargs))
        sandbox = SimpleNamespace(name=kwargs["name"], status="ready")
        self.sandboxes[sandbox.name] = sandbox
        return sandbox

    def start_sandbox(self, name, timeout):
        self.calls.append(("start", name))
        return self.sandboxes[name]

    def wait_for_sandbox(self, name, timeout):
        self.calls.append(("wait", name))
        return self.sandboxes[name]


@pytest.mark.parametrize(("status", "expected"), [(None, "create"), ("ready", None), ("stopped", "start"),
                                                    ("provisioning", "wait")])
def test_sandbox_per_case_is_found_started_or_created(status, expected):
    case_id = uuid.uuid4()
    name = sandbox_name(case_id)
    existing = [SimpleNamespace(name=name, status=status)] if status else []
    client = FakeSandboxClient(*existing)
    workspaces = LangSmithWorkspaces(client, idle_ttl_seconds=600, delete_after_stop_seconds=3600)

    sandbox = workspaces._get_or_create(name)
    assert sandbox.name == name
    assert [c[0] for c in client.calls] == ([expected] if expected else [])
    if expected == "create":
        assert client.calls[0][1] | {} == {"name": name, "idle_ttl_seconds": 600, "delete_after_stop_seconds": 3600,
                                          "timeout": 60}


def test_sandbox_created_concurrently_is_reused():
    name = sandbox_name(uuid.uuid4())

    class Racing(FakeSandboxClient):
        def create_sandbox(self, **kwargs):
            self.sandboxes[name] = SimpleNamespace(name=name, status="ready")  # the other run won
            raise RuntimeError("name already exists")

    assert LangSmithWorkspaces(Racing())._get_or_create(name).name == name

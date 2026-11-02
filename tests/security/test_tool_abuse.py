"""What a misbehaving or manipulated agent can do with its tools: read only its own case's tenant, write nothing."""

import uuid

from pathlib import Path

import pytest
from sqlalchemy import func, select

from finrecon_ai.agents.prompt_registry import all_versions, load_prompt
from finrecon_ai.agents.tools import TOOLS, ToolContext, ToolError, run_tool
from finrecon_ai.db.enums import ExceptionCategory, IngestionMethod, SourceType
from finrecon_ai.db.models import (
    AuditEvent,
    FinancialSource,
    InvestigationCase,
    ReconciliationException,
    Tenant,
    Transaction,
)
from tests.test_workflows import case_of, store  # noqa: F401 (fixture)

SQL_PAYLOADS = ["' OR '1'='1", "x'; DROP TABLE transactions; --", "%' UNION SELECT password_hash FROM users --",
                "\\' OR 1=1 --", "%", "_"]


@pytest.fixture
def ctx(session, world, case_ids, store):
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    return ToolContext(session=session, tenant_id=world[0].id, case=case,
                       exception=session.get(ReconciliationException, case.exception_id), policy_store=store)


def call(ctx, name, **args):
    return run_tool(ctx, TOOLS[name], {field: None for field in TOOLS[name].input_model.model_fields} | args)


@pytest.fixture
def foreign_transaction(session):
    other = Tenant(name="Victim", slug=f"victim-{uuid.uuid4().hex[:6]}")
    session.add(other)
    session.flush()
    source = FinancialSource(tenant_id=other.id, name="Victim Bank", source_type=SourceType.BANK_STATEMENT,
                             ingestion_method=IngestionMethod.CSV)
    session.add(source)
    session.flush()
    return other, source


def test_every_tool_is_read_only(session, ctx):
    session.flush()
    audit_before = session.scalar(select(func.count()).select_from(AuditEvent))
    for name, tool in TOOLS.items():
        args = {"query": "fees", "limit": 2} if name == "search_policies" else \
               {"counterparty": "x", "merchant": None} if name == "get_counterparty_history" else \
               {"reference": "STL"} if name == "search_transactions" else {}
        call(ctx, name, **args)
        assert not (session.new or session.dirty or session.deleted), f"{name} changed state"
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == audit_before


def test_tools_cannot_be_pointed_at_another_case(ctx):
    for name in ("get_case", "get_case_transactions", "get_match_explanation", "get_case_findings"):
        with pytest.raises(ToolError, match="invalid input"):
            run_tool(ctx, TOOLS[name], {"case_id": str(uuid.uuid4())})
    with pytest.raises(ToolError, match="invalid input"):
        call(ctx, "search_transactions", reference="x", tenant_id=str(uuid.uuid4()))


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payloads_in_tool_inputs_are_inert(session, ctx, payload):
    result = call(ctx, "search_transactions", reference=payload)
    # Wildcards are escaped: any hit must contain the payload literally ("_" does occur in IDs like pg_000109).
    for t in result["transactions"]:
        fields = (t["external_reference"], t["source_transaction_id"], t["description"])
        assert any(payload.lower() in (f or "").lower() for f in fields)
    call(ctx, "get_counterparty_history", counterparty=payload)
    assert session.scalar(select(func.count()).select_from(Transaction)) > 0, "nothing was dropped"


def test_search_never_returns_another_tenants_data(session, ctx, foreign_transaction):
    other, source = foreign_transaction
    found = call(ctx, "search_transactions", counterparty="Victim")
    assert found["transactions"] == []
    similar = call(ctx, "find_similar_cases")
    other_cases = session.scalars(select(InvestigationCase.id).where(InvestigationCase.tenant_id == other.id)).all()
    assert not {c["case_id"] for c in similar["cases"]} & {str(c) for c in other_cases}


def test_prompts_only_allow_existing_read_tools():
    for agent, version in all_versions():
        manifest = load_prompt(agent, version).manifest
        assert set(manifest.tools) <= set(TOOLS)
        assert not any(word in tool for tool in manifest.tools for word in ("write", "update", "delete", "approve",
                                                                            "execute", "transition"))


def test_oversized_tool_result_is_refused_not_truncated(session, ctx, monkeypatch):
    from finrecon_ai.agents import runtime

    monkeypatch.setattr(runtime, "MAX_TOOL_RESULT_CHARS", 50)
    tool = runtime.langchain_tool(TOOLS["get_case_transactions"], ctx)
    result = tool.invoke({"type": "tool_call", "name": tool.name, "args": {}, "id": "t1"})
    assert result.status == "error" and "too large" in result.content


def test_deep_agent_scratch_files_stay_in_run_state(session, ctx, tmp_path_factory, monkeypatch):
    """Deep Agents' write_file/edit_file/delete act on files in the run's own state, never on disk or the DB."""
    from finrecon_ai.agents.prompt_registry import load_prompt
    from finrecon_ai.agents.runtime import run_agent
    from finrecon_ai.db.enums import AgentName
    from tests.fakes import REPORT, FakeClient, final, reply, tool_use

    workdir = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(workdir)
    write = tool_use("write_file", {"file_path": "/notes.md", "content": "scratch"})
    client = FakeClient(reply("tool_use", write), reply("tool_use", tool_use("read_file", {"file_path": "/notes.md"})),
                        final(REPORT))
    result = run_agent(session, spec=load_prompt(AgentName.INVESTIGATION), ctx=ctx, user_content="Investigate.",
                       client=client)
    assert [c["ok"] for c in result.execution.tool_calls] == [True, True]
    assert "scratch" in client.calls[2]["messages"][-1].content, "the run can read back its own note"
    assert not list(workdir.iterdir()) and not Path("/notes.md").exists()

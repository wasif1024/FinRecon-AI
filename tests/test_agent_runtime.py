"""Agent runtime with a scripted Claude client: tool loop, allow-listing, evidence tracking, records, audit."""

import json

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select

from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.agents.runtime import AgentFailed, run_agent
from finrecon_ai.agents.schemas import InvestigationReport, strict_json_schema
from finrecon_ai.agents.tools import TOOLS, ToolContext
from finrecon_ai.db.enums import AgentExecutionStatus, AgentName, ExceptionCategory
from finrecon_ai.db.models import AuditEvent, InvestigationCase, ReconciliationException
from tests.fakes import REPORT, FakeClient, final, reply, tool_errors, tool_use
from tests.test_agent_tools import context

SPEC = load_prompt(AgentName.INVESTIGATION)


@pytest.fixture
def ctx(session, world, case_ids):
    tenant, _, _ = world
    case = session.get(InvestigationCase, case_ids[0])
    return ToolContext(session=session, tenant_id=tenant.id, case=case,
                       exception=session.get(ReconciliationException, case.exception_id))


def run(session, ctx, client):
    return run_agent(session, spec=SPEC, ctx=ctx, user_content=f"Investigate {ctx.case.case_number}.", client=client)


def audit_types(session, execution_id):
    return session.scalars(select(AuditEvent.event_type).where(AuditEvent.agent_execution_id == execution_id)
                           .order_by(AuditEvent.id)).all()


def test_tool_loop_produces_validated_report(session, ctx):
    client = FakeClient(
        reply("tool_use", tool_use("get_case", block_id="t1"), tool_use("get_case_transactions", block_id="t2")),
        final(REPORT),
    )
    result = run(session, ctx, client)

    assert isinstance(result.output, InvestigationReport)
    execution = result.execution
    assert execution.status is AgentExecutionStatus.COMPLETED
    assert execution.workflow_version == SPEC.ref
    assert sorted(c["tool"] for c in execution.tool_calls) == ["get_case", "get_case_transactions"]
    assert execution.input_tokens == 200 and execution.output_tokens == 100, "summed over both model calls"
    assert result.seen["transaction"], "transactions returned by tools are tracked"
    assert audit_types(session, execution.id) == ["agent.tool_invoked", "agent.tool_invoked",
                                                  "agent.execution_completed"]

    # Both results go back after the assistant turn, matched by tool call id.
    second = client.calls[1]["messages"]
    assert [m.tool_call_id for m in second if isinstance(m, ToolMessage)] == ["t1", "t2"]


def test_parallel_tool_calls_share_the_session_safely(session, ctx):
    """Regression: Claude often calls several tools at once and LangGraph runs them on worker threads; they all use
    the run's one database session ("Session is already flushing" in the first live run)."""
    names = ["get_case", "get_case_transactions", "get_match_explanation", "find_similar_cases"] * 4
    args = {"find_similar_cases": {"category": None}}
    client = FakeClient(reply("tool_use", *[tool_use(n, args.get(n)) for n in names]), final(REPORT))
    result = run(session, ctx, client)

    assert result.execution.status is AgentExecutionStatus.COMPLETED
    assert len(result.execution.tool_calls) == len(names) and all(c["ok"] for c in result.execution.tool_calls)
    assert audit_types(session, result.execution.id).count("agent.tool_invoked") == len(names)


def test_deep_agent_harness(session, ctx):
    """Case tools and subagents from the manifest; Deep Agents' planning and scratch files; no shell, no
    general-purpose subagent."""
    client = FakeClient(final(REPORT))
    run(session, ctx, client)
    tools = set(client.calls[0]["tools"])

    assert set(SPEC.manifest.tools) <= tools
    assert {"write_todos", "write_file", "read_file", "task"} <= tools
    assert "execute" not in tools
    task = next(t for t in client.calls[0]["raw_tools"] if getattr(t, "name", None) == "task")
    for subagent in SPEC.manifest.subagents:
        assert f"- {subagent.name}:" in task.description
    assert "- general-purpose:" not in task.description, "no catch-all subagent inheriting every tool"
    assert SPEC.system_text.strip()[:200] in client.calls[0]["system"]


def test_subagent_tool_calls_are_audited_and_tracked(session, world, case_ids):
    ctx = context(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)  # a case the engine matched
    def delegate(request):
        return reply("tool_use", tool_use("task", {"description": "find the settlement", "subagent_type":
                                                   "transaction-tracer"}))

    steps = [delegate,
             reply("tool_use", tool_use("get_match_explanation")),  # the subagent's turn
             reply("end_turn", "Matched settlement found."),        # the subagent reports back
             final(REPORT)]
    result = run(session, ctx, FakeClient(*steps))

    calls = result.execution.tool_calls
    assert [(c["tool"], c.get("subagent")) for c in calls] == [("get_match_explanation", "transaction-tracer"),
                                                               ("task", None)]
    assert result.seen["transaction"], "IDs a subagent's tools returned may be cited by the parent"


def test_request_shape_sent_to_claude(session, ctx, monkeypatch):
    """The real ChatAnthropic request, intercepted at the Anthropic SDK (no network)."""
    from types import SimpleNamespace

    from anthropic.resources.beta.messages import Messages
    from anthropic.types.beta import BetaMessage

    from finrecon_ai.agents.llm import FALLBACK_BETA, anthropic_chat_model

    sent = []

    def create(self, **params):
        sent.append(params)
        message = BetaMessage.model_validate({
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(REPORT)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})
        return SimpleNamespace(parse=lambda: message, headers={})

    # with_raw_response wraps Messages.create and expects a raw response whose parse() gives the message.
    monkeypatch.setattr(Messages, "create", create)
    result = run(session, ctx, lambda manifest: anthropic_chat_model(manifest, api_key="sk-test"))
    params = sent[0]

    assert result.output.summary == REPORT["summary"]
    assert params["model"] == "claude-opus-5-5" and params["max_tokens"] == SPEC.manifest.max_tokens
    assert FALLBACK_BETA in params["betas"] and params["fallbacks"] == "default"
    assert params["system"][0]["text"] == SPEC.system_text
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    case_tools = {t["name"]: t for t in params["tools"] if t["name"] in SPEC.manifest.tools}
    assert set(case_tools) == set(SPEC.manifest.tools)
    assert all(t["strict"] and t["input_schema"] == TOOLS[n].definition()["input_schema"]
               for n, t in case_tools.items())
    assert params["output_config"]["effort"] == "high"
    assert params["output_config"]["format"]["schema"] == strict_json_schema(InvestigationReport)
    assert "tool_choice" not in params  # forced tool choice is a 400 on Opus 5.5


def test_unknown_tool_returns_error_and_continues(session, ctx):
    client = FakeClient(reply("tool_use", tool_use("delete_everything", block_id="x")), final(REPORT))
    result = run(session, ctx, client)

    assert any("delete_everything" in e for e in tool_errors(client.calls[1]))
    assert result.execution.status is AgentExecutionStatus.COMPLETED


def test_tool_input_error_is_reported_to_model(session, ctx):
    empty_search = {k: None for k in TOOLS["search_transactions"].input_model.model_fields}
    client = FakeClient(reply("tool_use", tool_use("search_transactions", empty_search)), final(REPORT))
    result = run(session, ctx, client)
    assert any("at least one filter" in e for e in tool_errors(client.calls[1]))
    assert result.execution.tool_calls[0]["ok"] is False


def test_invalid_output_fails_run(session, ctx):
    client = FakeClient(final({"summary": "missing fields"}))
    with pytest.raises(AgentFailed, match="did not match") as exc:
        run(session, ctx, client)
    assert exc.value.execution.status is AgentExecutionStatus.FAILED
    assert audit_types(session, exc.value.execution.id)[-1] == "agent.execution_failed"


def test_refusal_fails_run_with_category(session, ctx):
    client = FakeClient(reply("refusal", stop_details={"category": "cyber"}))
    with pytest.raises(AgentFailed, match="declined.*cyber"):
        run(session, ctx, client)


def test_turn_limit(session, ctx):
    loops = [reply("tool_use", tool_use("get_case")) for _ in range(SPEC.manifest.max_turns + 1)]
    with pytest.raises(AgentFailed, match="no final answer"):
        run(session, ctx, FakeClient(*loops))


def test_api_error_is_recorded_then_raised(session, ctx):
    with pytest.raises(ConnectionError):
        run(session, ctx, FakeClient(ConnectionError("connection reset")))
    error = session.scalar(select(AuditEvent.payload).where(AuditEvent.event_type == "agent.execution_failed"))
    assert "ConnectionError" in error["error"]


def test_strict_schema_closes_objects_and_keeps_property_names():
    from pydantic import BaseModel

    class Inner(BaseModel):
        title: str
        format: str | None

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    schema = strict_json_schema(Outer)
    assert schema["additionalProperties"] is False and schema["required"] == ["inner", "items"]
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert set(inner["properties"]) == {"title", "format"}
    assert "title" not in inner  # the schema keyword is stripped, the property is not

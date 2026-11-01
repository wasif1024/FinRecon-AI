"""Agent runtime with a scripted Claude client: tool loop, allow-listing, evidence tracking, records, audit."""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.agents.runtime import AgentFailed, run_agent
from finrecon_ai.agents.schemas import InvestigationReport, strict_json_schema
from finrecon_ai.agents.tools import TOOLS, ToolContext
from finrecon_ai.db.enums import AgentExecutionStatus, AgentName
from finrecon_ai.db.models import AuditEvent, InvestigationCase, ReconciliationException

SPEC = load_prompt(AgentName.INVESTIGATION, "v1")


def usage(i=100, o=50):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=10, cache_creation_input_tokens=0)


def tool_use(name, args=None, block_id=None):
    return SimpleNamespace(type="tool_use", id=block_id or f"toolu_{uuid.uuid4().hex[:8]}", name=name,
                           input=args or {})


def reply(stop_reason, *content, stop_details=None):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage(), stop_details=stop_details)


def final(report: dict):
    return reply("end_turn", SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=json.dumps(report)))


REPORT = {
    "summary": "Settlement fee is 1.06 higher than the gateway fee.",
    "evidence": [],
    "findings": [{"type": "hypothesis", "statement": "Gateway surcharge.", "confidence": "medium",
                  "evidence_labels": []}],
    "possible_explanation": "A surcharge was applied at settlement.",
    "confidence": "medium",
    "recommended_next_action": "Request the gateway fee schedule.",
}


class FakeClient:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def create(self, **params):
        self.calls.append({**params, "messages": list(params["messages"])})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(params) if callable(step) else step


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
    assert [c["tool"] for c in execution.tool_calls] == ["get_case", "get_case_transactions"]
    assert execution.input_tokens == 220 and execution.output_tokens == 100
    assert result.seen["transaction"], "transactions returned by tools are tracked"
    assert audit_types(session, execution.id) == ["agent.tool_invoked", "agent.tool_invoked",
                                                  "agent.execution_completed"]

    # Both results go back in ONE user message, matched by tool_use id.
    second = client.calls[1]["messages"]
    assert second[1]["role"] == "assistant"
    assert [r["tool_use_id"] for r in second[2]["content"]] == ["t1", "t2"]


def test_request_shape(session, ctx):
    client = FakeClient(final(REPORT))
    run(session, ctx, client)
    params = client.calls[0]

    assert params["model"] == "claude-opus-5-5"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["system"][0]["text"] == SPEC.system_text
    assert sorted(t["name"] for t in params["tools"]) == sorted(SPEC.manifest.tools)
    assert all(t["strict"] for t in params["tools"])
    assert params["output_config"]["effort"] == "high"
    assert params["output_config"]["format"]["schema"] == strict_json_schema(InvestigationReport)
    assert "tool_choice" not in params  # forced tool choice is a 400 on Opus 5.5


def test_tool_outside_allow_list_returns_error_and_continues(session, ctx):
    client = FakeClient(reply("tool_use", tool_use("delete_everything", block_id="x")), final(REPORT))
    result = run(session, ctx, client)

    tool_result = client.calls[1]["messages"][2]["content"][0]
    assert tool_result["is_error"] is True and "not available" in tool_result["content"]
    assert result.execution.tool_calls[0]["ok"] is False


def test_tool_input_error_is_reported_to_model(session, ctx):
    empty_search = {k: None for k in TOOLS["search_transactions"].input_model.model_fields}
    client = FakeClient(reply("tool_use", tool_use("search_transactions", empty_search)), final(REPORT))
    run(session, ctx, client)
    assert "at least one filter" in client.calls[1]["messages"][2]["content"][0]["content"]


def test_invalid_output_fails_run(session, ctx):
    client = FakeClient(final({"summary": "missing fields"}))
    with pytest.raises(AgentFailed, match="did not match") as exc:
        run(session, ctx, client)
    assert exc.value.execution.status is AgentExecutionStatus.FAILED
    assert audit_types(session, exc.value.execution.id)[-1] == "agent.execution_failed"


def test_refusal_fails_run_with_category(session, ctx):
    client = FakeClient(reply("refusal", stop_details=SimpleNamespace(category="cyber")))
    with pytest.raises(AgentFailed, match="declined.*cyber"):
        run(session, ctx, client)


def test_turn_limit(session, ctx):
    loops = [reply("tool_use", tool_use("get_case")) for _ in range(SPEC.manifest.max_turns)]
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

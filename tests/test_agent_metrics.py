"""Agent performance metrics: reliability, latency, cost, grounding and reviewer outcomes per prompt version."""

from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from finrecon_ai.agents.metrics import _cost, _percentile, agent_metrics
from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.authz import PermissionDenied
from finrecon_ai.db.enums import AgentName, ApprovalDecisionType, RoleName
from finrecon_ai.workflows.case_investigation import decide_and_resume, investigate_case
from tests.fakes import AgentScripts, final
from tests.test_api import auth, client, scripts  # noqa: F401 (fixtures)
from tests.test_approvals import paused, proposal_steps  # noqa: F401 (fixtures)
from tests.test_workflows import fee_case, investigation_steps, make_deps, store  # noqa: F401 (fixtures)


def by_agent(metrics):
    return {m.agent: m for m in metrics}


def test_metrics_after_an_investigation_and_approval(session, world, fee_case, paused, approver):
    deps, saver, request_id = paused(fee_case, "confirm_fee_discrepancy")
    decide_and_resume(deps, saver, approver, request_id, ApprovalDecisionType.APPROVE)

    metrics = by_agent(agent_metrics(session, world[2][RoleName.MANAGER]))
    investigation, resolution = metrics["investigation"], metrics["resolution"]
    assert investigation.prompt == load_prompt(AgentName.INVESTIGATION).ref
    assert (investigation.runs, investigation.completed, investigation.failed) == (1, 1, 0)
    assert investigation.success_rate == 1.0 and investigation.latency_p50_s is not None
    assert investigation.tool_calls == 1 and investigation.tool_errors == 0
    # The scripted investigation cites one invented ID and a "fact" resting on it: both are rejected on verification.
    assert investigation.runs_with_rejected_output == 1 and investigation.rejected_items >= 2
    assert resolution.outcomes == {"executed": 1}
    # Fake usage per call: 100 + 10 cached input, 50 output; two calls per agent.
    assert investigation.avg_input_tokens == 200 and investigation.avg_output_tokens == 100
    assert Decimal(investigation.est_cost_usd) == _cost("claude-opus-5-5", 200, 100).quantize(Decimal("0.01"))


def test_failures_are_counted(session, world, fee_case, make_deps):
    investigate_case(make_deps(AgentScripts(investigation=[final({"summary": "incomplete"})])), InMemorySaver(),
                     world[2][RoleName.MANAGER], fee_case.id)
    investigation = by_agent(agent_metrics(session, world[2][RoleName.MANAGER]))["investigation"]
    assert (investigation.runs, investigation.failed, investigation.success_rate) == (1, 1, 0.0)


def test_deterministic_pipeline_agents_cost_nothing(session, world, fee_case, paused):
    paused(fee_case)
    for metric in agent_metrics(session, world[2][RoleName.MANAGER]):
        if metric.model is None:
            assert metric.est_cost_usd == "0.00"


@pytest.mark.parametrize("role", [RoleName.ANALYST])
def test_analysts_cannot_read_metrics(world, role):
    with pytest.raises(PermissionDenied):
        agent_metrics(None, world[2][role])


def test_helpers():
    assert _cost("claude-opus-5-5", 1_000_000, 1_000_000) == Decimal("24")
    assert _cost(None, 10, 10) == 0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == pytest.approx(4.8)
    assert _percentile([7.0], 0.95) == 7.0


def test_metrics_endpoint_roles(client, case_ids):
    assert client.get("/metrics/agents", headers=auth(client, RoleName.MANAGER)).status_code == 200
    assert client.get("/metrics/agents", headers=auth(client, RoleName.ANALYST)).status_code == 403

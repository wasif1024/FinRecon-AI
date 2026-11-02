"""Graphs for the LangGraph Agent Server / Studio, and runs that live on it (no server, no Claude)."""

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from finrecon_ai import agent_server
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import RoleName
from finrecon_ai.workflows import case_investigation as wf
from tests.fakes import AgentScripts
from tests.test_workflows import fee_case, investigation_steps, make_deps, resolution_steps, store  # noqa: F401

DRAWING = SimpleNamespace(execution_runtime=None)  # how the server asks for a graph's schema or drawing
RUNNING = SimpleNamespace(execution_runtime=object())


@pytest.mark.parametrize("name", ["investigation_agent", "risk_analysis_agent", "resolution_agent"])
def test_agent_graphs_draw_but_do_not_run_alone(name):
    factory = getattr(agent_server, name)
    nodes = factory({"configurable": {}}, DRAWING).get_graph(xray=1).nodes
    assert {"model", "tools"} <= set(nodes)
    with pytest.raises(wf.WorkflowError, match="runs only inside case-investigation"):
        factory({"configurable": {}}, RUNNING)


def test_case_graph_opens_the_case_then_runs_the_workflow(monkeypatch):
    monkeypatch.setattr(agent_server, "_deps", lambda: wf.WorkflowDeps(None, None, None))
    graph = agent_server.case_investigation({"configurable": {}}, DRAWING)
    nodes = set(graph.get_graph(xray=1).nodes)
    assert {"open_case", "case_workflow:investigate", "case_workflow:await_decision"} <= nodes
    assert list(graph.get_input_jsonschema()["properties"]) == ["case_number"]


def test_graphs_refuse_outside_development(monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    for factory in (agent_server.case_investigation, agent_server.investigation_agent):
        with pytest.raises(RuntimeError, match="for development"):
            factory({"configurable": {}}, DRAWING)


def test_approval_resumes_a_run_where_it_lives(session, world, fee_case, make_deps, monkeypatch):
    """A case started on the Agent Server (Studio) is resumed there; one started here, from our checkpoints."""
    sent = []
    monkeypatch.setattr(wf, "resume_on_agent_server",
                        lambda thread, decision: sent.append((thread, decision)) or wf.WorkflowResult(thread, False, {}))
    fee_case.workflow_thread_id = "4f0c1d2e-studio-thread"
    session.flush()
    wf.resume_case(make_deps(None), InMemorySaver(), fee_case.id, {"approval_decision_id": "d1"})
    assert sent == [("4f0c1d2e-studio-thread", {"approval_decision_id": "d1"})]


def test_start_checks_the_agent_server_thread(session, world, fee_case, make_deps, monkeypatch):
    manager = world[2][RoleName.MANAGER]
    fee_case.workflow_thread_id = "studio-thread"
    session.flush()
    monkeypatch.setattr(wf, "agent_server_thread_pending", lambda thread: True)

    with pytest.raises(wf.WorkflowError, match="running on the Agent Server"):
        wf.authorize_start(make_deps(None), InMemorySaver(), manager, fee_case.id)
    # The Studio run doing the check is not "another run".
    wf.authorize_start(make_deps(None), InMemorySaver(), manager, fee_case.id, current_thread="studio-thread")

    monkeypatch.setattr(wf, "agent_server_thread_pending", lambda thread: False)  # that run has finished
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    assert wf.investigate_case(make_deps(llm), InMemorySaver(), manager, fee_case.id).paused_for_approval
    session.refresh(fee_case)
    assert fee_case.workflow_thread_id == wf.thread_id_for(fee_case.id), "the case now lives in our checkpoints"


def test_unreachable_agent_server_counts_as_pending(monkeypatch):
    class Down:
        threads = SimpleNamespace(get_state=lambda thread: (_ for _ in ()).throw(ConnectionError("down")))

    monkeypatch.setattr(wf, "_agent_server_client", lambda: Down)
    assert wf.agent_server_thread_pending("t") is True

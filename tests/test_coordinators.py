"""Coordinator Deep Agents: the ops assistant and the background batch investigator (scripted model, fake server)."""

import itertools
from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from finrecon_ai import coordinators
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import CaseStatus, ExceptionCategory
from finrecon_ai.workflows.case_investigation import WorkflowDeps
from tests.fakes import FakeClient, reply, tool_use
from tests.test_workflows import case_of


class FakeAgentServer:
    def __init__(self):
        self.started: list[tuple[str, str, dict]] = []
        ids = itertools.count(1)
        server = self

        class Threads:
            def create(self, metadata=None):
                return {"thread_id": f"t{next(ids)}"}

            def get_state(self, thread_id):
                return {"next": ["case_workflow"], "tasks": [{"interrupts": [{"value": {}}]}]}

        class Runs:
            def create(self, thread_id, graph_id, input):
                server.started.append((thread_id, graph_id, input))
                return {"run_id": f"r-{thread_id}"}

            def list(self, thread_id, limit=1):
                return [{"status": "success"}]

        self.threads, self.runs = Threads(), Runs()


@pytest.fixture
def tools(session, case_ids):
    @contextmanager
    def unit_of_work():
        yield session

    server = FakeAgentServer()

    @contextmanager
    def checkpointer():
        yield InMemorySaver()

    return coordinators.case_tools(WorkflowDeps(unit_of_work, None, None), checkpointer, server), server


def test_case_tools_read_and_start_through_the_workflow(session, world, tools):
    tools, server = tools
    fee = tools["list_cases"].invoke({"status": "new", "category": "fee_discrepancy"})
    assert fee and all(c["category"] == "fee_discrepancy" for c in fee)
    assert fee[0]["title"].startswith("Fee discrepancy"), "titles are marked as data"

    started = tools["start_case_investigation"].invoke({"case_number": fee[0]["case_number"]})
    assert started["started"] and server.started == [(started["thread_id"], "case-investigation",
                                                      {"case_number": fee[0]["case_number"]})]
    assert tools["case_run_status"].invoke({"thread_id": started["thread_id"]})["waiting_for_approval"]


def test_cases_that_cannot_start_are_refused_without_a_run(session, world, tools):
    tools, server = tools
    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    case.status = CaseStatus.AWAITING_APPROVAL
    session.flush()
    result = tools["start_case_investigation"].invoke({"case_number": case.case_number})
    assert result == {"case_number": case.case_number, "started": False, "reason": "case is awaiting_approval"}
    assert tools["start_case_investigation"].invoke({"case_number": "CASE-999999"})["reason"] == "not found"
    assert server.started == []


def test_batch_is_capped(session, world, tools, monkeypatch):
    """However many starts the model asks for, at most BATCH_MAX_CASES run (each calls Claude)."""
    tools, server = tools
    monkeypatch.setattr(get_settings(), "batch_max_cases", 2)
    numbers = [c["case_number"] for c in tools["list_cases"].invoke({"status": "new", "limit": 5})]
    starts = [tool_use("start_case_investigation", {"case_number": n}) for n in numbers]
    client = FakeClient(reply("tool_use", *starts), reply("end_turn", "Done."))
    agent = coordinators.build_batch_investigator(tools, model=client(None))
    agent.invoke({"messages": [{"role": "user", "content": "Investigate the new cases."}]})
    assert len(server.started) == 2


def test_ops_assistant_starts_the_batch_as_an_async_subagent(tools):
    tools, _ = tools
    client = FakeClient(reply("end_turn", "Hello."))
    coordinators.build_ops_assistant(tools, model=client(None)).invoke(
        {"messages": [{"role": "user", "content": "hi"}]})
    offered = set(client.calls[0]["tools"])
    assert {"start_async_task", "check_async_task", "list_async_tasks", "list_cases", "case_status"} <= offered
    assert "start_case_investigation" not in offered, "only the batch agent starts investigations"
    assert "execute" not in offered

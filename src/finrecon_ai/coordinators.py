"""Coordinator Deep Agents on the Agent Server: an operations assistant and a background batch investigator.

Following Deep-Sales-Assistant's main agent + async subagent pattern: `ops-assistant` (chat, in Studio) starts
`batch-investigator` with `start_async_task` and carries on; the batch agent starts one `case-investigation` run per
case on the same server and reports back. Neither investigates or decides anything itself: the case workflow does
the audited work and pauses for a manager's approval, and every start goes through that workflow's checks.

Both act as the Studio user (STUDIO_ACTOR_EMAIL). Their tools read cases; the only action is starting a case's
investigation, capped at BATCH_MAX_CASES per batch because each one calls Claude.
"""

from functools import cache
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgentMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import StructuredTool
from sqlalchemy import select

from finrecon_ai.agents.llm import claude
from finrecon_ai.agents.prompt_registry import PROMPTS_DIR
from finrecon_ai.authz import Actor, load_actor
from finrecon_ai.cases.service import list_cases
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import CaseStatus, ExceptionCategory
from finrecon_ai.db.models import InvestigationCase, ReconciliationException, Tenant, User
from finrecon_ai.db.tenancy import bypass_rls
from finrecon_ai.workflows.case_investigation import (
    STARTABLE,
    WorkflowDeps,
    WorkflowError,
    as_data,
    workflow_state,
)

COORDINATOR_MODEL = "claude-sonnet-5-5"
OPS_GRAPH_ID = "ops-assistant"
BATCH_GRAPH_ID = "batch-investigator"


def coordinator_prompt(name: str) -> str:
    return (PROMPTS_DIR / "coordinators" / f"{name}.md").read_text().strip() + "\n"


def studio_actor(session) -> Actor:
    """The user Agent Server runs act as (development only; see agent_server)."""
    settings = get_settings()
    bypass_rls(session)  # finding the configured tenant and user; everything after runs tenant-scoped
    tenant = session.scalar(select(Tenant).where(Tenant.slug == settings.studio_tenant_slug))
    user = tenant and session.scalar(select(User).where(User.tenant_id == tenant.id,
                                                        User.email == settings.studio_actor_email))
    if not (tenant and user):
        raise WorkflowError("STUDIO_TENANT_SLUG / STUDIO_ACTOR_EMAIL do not name an existing user")
    return load_actor(session, tenant.id, user.id)


def case_tools(deps: WorkflowDeps, checkpointer_factory, agent_server) -> dict[str, StructuredTool]:
    """`agent_server` is a langgraph_sdk sync client for the server these graphs run on."""

    def _list_cases(status: str = "new", category: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Cases in a status (new, investigating, awaiting_approval, ...), optionally of one exception category."""
        with deps.unit_of_work() as session:
            actor = studio_actor(session)
            cases = list_cases(session, actor, status=CaseStatus(status))
            categories = dict(session.execute(
                select(ReconciliationException.id, ReconciliationException.category)
                .where(ReconciliationException.id.in_({c.exception_id for c in cases}))).all())
            rows = [{"case_number": c.case_number, "category": str(categories.get(c.exception_id)),
                     "priority": str(c.priority), "status": str(c.status), "title": as_data(c.title)}
                    for c in cases if category is None or categories.get(c.exception_id) == ExceptionCategory(category)]
        return rows[:max(1, min(limit, 50))]

    def _case_status(case_number: str) -> dict[str, Any]:
        """A case's status and where its workflow stands (running, waiting for approval, finished)."""
        with deps.unit_of_work() as session:
            actor = studio_actor(session)
            case = session.scalar(select(InvestigationCase).where(InvestigationCase.tenant_id == actor.tenant_id,
                                                                  InvestigationCase.case_number == case_number))
            if case is None:
                return {"error": f"case {case_number} not found"}
            status, thread = str(case.status), case.workflow_thread_id
        if thread and not thread.startswith("case:"):
            pending = agent_server.threads.get_state(thread).get("next") or []
        else:
            with checkpointer_factory() as checkpointer:
                pending, _ = workflow_state(deps, checkpointer, case.id)
        return {"case_number": case_number, "status": status, "workflow_next": list(pending)}

    def _start_case_investigation(case_number: str) -> dict[str, Any]:
        """Start one case's investigation workflow in the background. It runs the investigation agents (calls
        Claude), is audited, and pauses for a manager's approval. Returns the run to check later."""
        from finrecon_ai.agent_server import CASE_GRAPH_ID

        with deps.unit_of_work() as session:
            actor = studio_actor(session)
            case = session.scalar(select(InvestigationCase).where(InvestigationCase.tenant_id == actor.tenant_id,
                                                                  InvestigationCase.case_number == case_number))
            if case is None:
                return {"case_number": case_number, "started": False, "reason": "not found"}
            if case.status not in STARTABLE:
                return {"case_number": case_number, "started": False, "reason": f"case is {case.status}"}
        thread = agent_server.threads.create(metadata={"case_number": case_number, "started_by": BATCH_GRAPH_ID})
        run = agent_server.runs.create(thread["thread_id"], CASE_GRAPH_ID, input={"case_number": case_number})
        return {"case_number": case_number, "started": True, "thread_id": thread["thread_id"],
                "run_id": run["run_id"]}

    def _case_run_status(thread_id: str) -> dict[str, Any]:
        """Status of a case investigation started with start_case_investigation."""
        runs = agent_server.runs.list(thread_id, limit=1)
        state = agent_server.threads.get_state(thread_id)
        error = next((t.get("error") for t in state.get("tasks", []) if t.get("error")), None)
        waiting = "case_workflow" in (state.get("next") or []) or any(
            t.get("interrupts") for t in state.get("tasks", []))
        return {"thread_id": thread_id, "run_status": runs[0]["status"] if runs else "unknown",
                "waiting_for_approval": bool(waiting), "error": error}

    functions = [_list_cases, _case_status, _start_case_investigation, _case_run_status]
    return {f.__name__.lstrip("_"): StructuredTool.from_function(f, name=f.__name__.lstrip("_")) for f in functions}


@cache
def _model(effort: str = "medium"):
    return claude(COORDINATOR_MODEL, effort=effort, max_tokens=8000, api_key=get_settings().anthropic_api_key)


def build_batch_investigator(tools: dict[str, StructuredTool], model=None):
    settings = get_settings()
    return create_deep_agent(
        model=model or _model(),
        tools=[tools["list_cases"], tools["start_case_investigation"], tools["case_run_status"]],
        system_prompt=coordinator_prompt("batch_investigator"),
        middleware=[ToolCallLimitMiddleware(tool_name="start_case_investigation", run_limit=settings.batch_max_cases,
                                            exit_behavior="continue"),
                    ModelCallLimitMiddleware(run_limit=40, exit_behavior="end")],
        name=BATCH_GRAPH_ID,
    )


def build_ops_assistant(tools: dict[str, StructuredTool], model=None):
    return create_deep_agent(
        model=model or _model(),
        tools=[tools["list_cases"], tools["case_status"]],
        system_prompt=coordinator_prompt("ops_assistant"),
        middleware=[AsyncSubAgentMiddleware(async_subagents=[{
            "name": BATCH_GRAPH_ID,
            "description": ("Starts investigations for a batch of cases in the background and reports which are "
                            "now waiting for approval. Say which cases: a category, a status, a number, or case "
                            f"numbers (at most {get_settings().batch_max_cases} per batch)."),
            "graph_id": BATCH_GRAPH_ID,
        }]), ModelCallLimitMiddleware(run_limit=20, exit_behavior="end")],
        name=OPS_GRAPH_ID,
    )

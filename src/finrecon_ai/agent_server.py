"""Graphs served by the LangGraph Agent Server (see langgraph.json), so they open in LangSmith Studio.

    uv run langgraph dev        # then Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024

- `case-investigation`: the whole case workflow, the same graph the API and CLI run. Input
  `{"case_number": "CASE-000021"}`. It runs as the Studio user (`STUDIO_ACTOR_EMAIL` in `STUDIO_TENANT_SLUG`)
  through the same checks as the API (permission, assignment, status, no other run in progress), records and
  audits every agent run, and pauses for approval. Only the approval service can decide: record the decision in
  the dashboard or with `finrecon approvals decide`, which resumes this thread here; a resume value typed into
  Studio is ignored unless it names that recorded decision.
- `investigation-agent`, `risk-analysis-agent`, `resolution-agent`: each Deep Agent's own graph (model, case
  tools, subagents, planning, middleware), built from the active prompt version, to inspect in Studio. They do
  not run on their own: every agent run goes through `case-investigation`, so it is recorded and audited.

Development only: the factories refuse to build when `ENVIRONMENT` is not `development`. The server keeps its own
threads and checkpoints (in memory for `langgraph dev`; PostgreSQL in a deployed Agent Server).
"""

from functools import cache
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph_sdk.runtime import ServerRuntime
from sqlalchemy import select

from finrecon_ai.agents.llm import anthropic_chat_model, default_client
from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.agents.runtime import build_deep_agent
from finrecon_ai.authz import load_actor
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import AgentName
from finrecon_ai.db.models import InvestigationCase, Tenant, User
from finrecon_ai.db.tenancy import bypass_rls
from finrecon_ai.log import get_logger
from finrecon_ai.workflows.case_investigation import (
    CaseState,
    WorkflowDeps,
    WorkflowError,
    authorize_start,
    build_case_graph,
    initial_state,
    postgres_checkpointer,
    session_unit_of_work,
)

log = get_logger(__name__)

CASE_GRAPH_ID = "case-investigation"


class StudioInput(TypedDict):
    case_number: str


class StudioState(CaseState, total=False):
    case_number: str


def _require_development() -> None:
    if get_settings().environment != "development":
        raise RuntimeError("the Agent Server graphs are for development; set ENVIRONMENT=development")


@cache
def _deps() -> WorkflowDeps:
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.tracing import configure_tracing

    configure_tracing(get_settings())
    return WorkflowDeps(unit_of_work=session_unit_of_work(), llm=default_client(), policy_store=default_store())


def _open_case(deps: WorkflowDeps, state: StudioState, config: dict) -> dict[str, Any]:
    """Resolve the case and the Studio user, then apply the same checks as starting a run from the API."""
    settings = get_settings()
    with deps.unit_of_work() as session:
        bypass_rls(session)  # finding the configured tenant and user; everything after runs tenant-scoped
        tenant = session.scalar(select(Tenant).where(Tenant.slug == settings.studio_tenant_slug))
        user = tenant and session.scalar(select(User).where(User.tenant_id == tenant.id,
                                                            User.email == settings.studio_actor_email))
        case_id = tenant and session.scalar(select(InvestigationCase.id).where(
            InvestigationCase.tenant_id == tenant.id, InvestigationCase.case_number == state.get("case_number")))
        if not (tenant and user):
            raise WorkflowError("STUDIO_TENANT_SLUG / STUDIO_ACTOR_EMAIL do not name an existing user")
        if case_id is None:
            raise WorkflowError(f"case {state.get('case_number')!r} not found in {tenant.slug}")
        actor = load_actor(session, tenant.id, user.id)
    with postgres_checkpointer() as checkpointer:
        authorize_start(deps, checkpointer, actor, case_id, current_thread=config["configurable"].get("thread_id"))
    log.info("studio.case_opened", case_id=case_id, actor=str(actor.user_id))
    return dict(initial_state(actor, case_id))


def case_investigation(config: RunnableConfig, runtime: ServerRuntime):
    """`case-investigation`: open the case as the Studio user, then the case workflow as a subgraph."""
    _require_development()
    deps = _deps()
    graph = StateGraph(StudioState, input_schema=StudioInput)
    graph.add_node("open_case", lambda state, config: _open_case(deps, state, config))
    # No checkpointer of its own: the Agent Server's checkpointer covers the subgraph and its Deep Agents.
    graph.add_node("case_workflow", build_case_graph(deps, checkpointer=None))
    graph.add_edge(START, "open_case")
    graph.add_edge("open_case", "case_workflow")
    return graph.compile(name=CASE_GRAPH_ID)


def _agent_view(agent: AgentName):
    def factory(config: RunnableConfig, runtime: ServerRuntime):
        _require_development()
        if runtime.execution_runtime is not None:
            raise WorkflowError(f"{agent.value} runs only inside {CASE_GRAPH_ID}, so every run is recorded and "
                                "audited; start that graph with a case_number")
        spec = load_prompt(agent)
        return build_deep_agent(spec, anthropic_chat_model(spec.manifest, api_key="unused-for-drawing"), ctx=None)

    factory.__name__ = f"{agent.value}_agent"
    return factory


investigation_agent = _agent_view(AgentName.INVESTIGATION)
risk_analysis_agent = _agent_view(AgentName.RISK_ANALYSIS)
resolution_agent = _agent_view(AgentName.RESOLUTION)

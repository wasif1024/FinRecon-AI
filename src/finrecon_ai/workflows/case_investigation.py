"""LangGraph workflow for one investigation case (SRS §8, steps 7-10).

    start -> investigate -> [assess_risk] -> propose_resolution -> request_approval -> await_decision (pause)
                 ^                                                                          |
                 +----------------------- "return_for_investigation" ----------------------+
                                                                          "approve" -> execute_resolution

Each node is one unit of work: it opens a session, does its work through the deterministic services (agents
only read), and commits. Node state holds IDs only, so a checkpoint never carries case data. The graph pauses
at await_decision with a LangGraph interrupt; decide_and_resume records a reviewer's decision and resumes it.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, TypedDict

import anthropic
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.agents.llm import ChatModelFactory
from finrecon_ai.agents.persistence import persist_findings, persist_recommendation
from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.agents.runtime import AgentFailed, run_agent
from finrecon_ai.agents.tools import ToolContext
from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor, Permission, load_actor
from finrecon_ai.cases.service import require_assignment, get_case, transition_case
from finrecon_ai.db.enums import (
    ActorType,
    AgentName,
    ApprovalDecisionType,
    CaseStatus,
    ExceptionCategory,
    RecommendationStatus,
    RoleName,
)
from finrecon_ai.db.models import ApprovalRequest, InvestigationCase, ReconciliationException, ResolutionRecommendation
from finrecon_ai.db.tenancy import bind_tenant, bypass_rls
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.policies.store import PolicyStore

log = get_logger(__name__)

WORKFLOW_VERSION = "case_investigation@1"
RISK_CATEGORIES = {ExceptionCategory.ANOMALY, ExceptionCategory.POTENTIAL_DUPLICATE,
                   ExceptionCategory.CONFIRMED_DUPLICATE}
STARTABLE = {CaseStatus.NEW, CaseStatus.INVESTIGATING}
# Network blips, rate limits and overload are retried; anything else fails the node.
AGENT_RETRY = RetryPolicy(max_attempts=3, initial_interval=2.0, retry_on=(
    anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError))


class CaseState(TypedDict, total=False):
    tenant_id: str
    case_id: str
    requested_by: str
    round: int
    category: str
    investigation_execution_id: str
    risk_execution_id: str | None
    resolution_execution_id: str
    recommendation_id: str
    approval_request_id: str
    decision: dict[str, Any] | None
    rejected_output: list[str]
    execution_result: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class WorkflowDeps:
    unit_of_work: Callable[[], AbstractContextManager[Session]]
    llm: ChatModelFactory
    policy_store: PolicyStore | None


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class WorkflowResult:
    thread_id: str
    paused_for_approval: bool
    state: dict[str, Any]


def thread_id_for(case_id: uuid.UUID) -> str:
    return f"case:{case_id}"


def build_case_graph(deps: WorkflowDeps, checkpointer: Any):
    graph = StateGraph(CaseState)
    graph.add_node("start", lambda state, config: _start(deps, state, config))
    graph.add_node("investigate", lambda state: _investigate(deps, state), retry_policy=AGENT_RETRY)
    graph.add_node("assess_risk", lambda state: _assess_risk(deps, state), retry_policy=AGENT_RETRY)
    graph.add_node("propose_resolution", lambda state: _propose(deps, state), retry_policy=AGENT_RETRY)
    graph.add_node("request_approval", lambda state, config: _request_approval(deps, state, config))
    graph.add_node("await_decision", lambda state: _await_decision(deps, state))
    graph.add_node("execute_resolution", lambda state: _execute(deps, state))
    graph.add_node("failed", lambda state: _failed(deps, state))

    graph.add_edge(START, "start")
    graph.add_edge("start", "investigate")
    graph.add_conditional_edges("investigate", _after_investigation,
                                ["assess_risk", "propose_resolution", "failed"])
    graph.add_conditional_edges("assess_risk", lambda s: "failed" if s.get("error") else "propose_resolution",
                                ["propose_resolution", "failed"])
    graph.add_conditional_edges("propose_resolution", lambda s: "failed" if s.get("error") else "request_approval",
                                ["request_approval", "failed"])
    graph.add_edge("request_approval", "await_decision")
    graph.add_conditional_edges("await_decision", _after_decision, ["investigate", "execute_resolution", END])
    graph.add_edge("execute_resolution", END)
    graph.add_edge("failed", END)
    return graph.compile(checkpointer=checkpointer)


# --- public API ---


def investigate_case(deps: WorkflowDeps, checkpointer: Any, actor: Actor, case_id: uuid.UUID) -> WorkflowResult:
    """Run the workflow until it pauses for approval (or fails). Permission-checked as the requesting user."""
    authorize_start(deps, checkpointer, actor, case_id)
    graph = build_case_graph(deps, checkpointer)
    config = _run_config(actor.tenant_id, case_id, "case_investigation")
    with log_context(case_id=case_id, workflow=WORKFLOW_VERSION):
        state = graph.invoke(initial_state(actor, case_id), config)
    return WorkflowResult(thread_id_for(case_id), bool(graph.get_state(config).next), dict(state))


def authorize_start(deps: WorkflowDeps, checkpointer: Any, actor: Actor, case_id: uuid.UUID,
                    current_thread: str | None = None) -> None:
    """The checks before any run starts, wherever it runs (API, CLI, Agent Server): permission, assignment,
    status, and no other run of this case still in progress or waiting for a decision. `current_thread` is the
    Agent Server thread doing the check, which is of course in progress itself."""
    actor.require(Permission.CASE_WORK)
    with deps.unit_of_work() as session:
        case = get_case(session, actor, case_id)
        require_assignment(actor, case)
        if case.status not in STARTABLE:
            raise WorkflowError(f"case is {case.status}; investigation can start only from new or investigating")
        thread = case.workflow_thread_id
    if build_case_graph(deps, checkpointer).get_state({"configurable": {"thread_id": thread_id_for(case_id)}}).next:
        raise WorkflowError("this case's workflow is already running or waiting for a decision")
    if thread and thread not in (thread_id_for(case_id), current_thread) and agent_server_thread_pending(thread):
        raise WorkflowError(f"this case's workflow is running on the Agent Server (thread {thread})")


def initial_state(actor: Actor, case_id: uuid.UUID) -> CaseState:
    return {"tenant_id": str(actor.tenant_id), "case_id": str(case_id), "requested_by": str(actor.user_id),
            "round": 1, "error": None, "decision": None, "risk_execution_id": None, "rejected_output": []}


def resume_case(deps: WorkflowDeps, checkpointer: Any, case_id: uuid.UUID, decision: dict[str, Any]) -> WorkflowResult:
    """Continue a paused workflow with a reviewer's decision, wherever its run lives. Called by the approval
    service: runs started by the API or CLI are in this database's checkpoints; runs started on the Agent Server
    (Studio) are resumed there."""
    with deps.unit_of_work() as session:
        bypass_rls(session)  # the decision was authorised by the approval service; this only finds the thread
        thread = session.scalar(select(InvestigationCase.workflow_thread_id)
                                .where(InvestigationCase.id == case_id))
    if thread and thread != thread_id_for(case_id):
        return resume_on_agent_server(thread, decision)
    graph = build_case_graph(deps, checkpointer)
    config = _run_config(None, case_id, f"case_resume:{decision.get('decision')}")
    if graph.get_state(config).next != ("await_decision",):
        raise WorkflowError("this case's workflow is not waiting for a decision")
    with log_context(case_id=case_id, workflow=WORKFLOW_VERSION):
        state = graph.invoke(Command(resume=decision), config)
    return WorkflowResult(thread_id_for(case_id), bool(graph.get_state(config).next), dict(state))


def decide_and_resume(deps: WorkflowDeps, checkpointer: Any, actor: Actor, request_id: uuid.UUID,
                      decision: ApprovalDecisionType, *, comment: str | None = None,
                      evidence_considered_ids: list[uuid.UUID] | None = None) -> WorkflowResult:
    """Record the decision (committed on its own), then resume the paused workflow with it."""
    from finrecon_ai.approvals.service import decide, resume_payload

    with deps.unit_of_work() as session:
        row = decide(session, actor, request_id, decision, comment=comment,
                     evidence_considered_ids=evidence_considered_ids)
        case_id = session.get(ApprovalRequest, request_id).case_id
        payload = resume_payload(row)
    return resume_case(deps, checkpointer, case_id, payload)


def resume_decided(deps: WorkflowDeps, checkpointer: Any, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    """Recovery: resume workflows whose decision was recorded but whose resume did not complete."""

    from finrecon_ai.approvals.service import resume_payload
    from finrecon_ai.db.enums import ApprovalRequestStatus
    from finrecon_ai.db.models import ApprovalDecision

    from finrecon_ai.db.tenancy import bind_tenant

    with deps.unit_of_work() as session:
        bind_tenant(session, tenant_id)
        rows = session.execute(
            select(ApprovalRequest.id, ApprovalRequest.case_id, ApprovalDecision)
            .join(ApprovalDecision, ApprovalDecision.approval_request_id == ApprovalRequest.id)
            .where(ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == ApprovalRequestStatus.DECIDED)
        ).all()
        decided = [(request_id, case_id, resume_payload(row)) for request_id, case_id, row in rows]

    graph = build_case_graph(deps, checkpointer)
    resumed = []
    for request_id, case_id, payload in decided:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id_for(case_id)}})
        # Only a thread still paused on this very request; later rounds have their own requests.
        if snapshot.next == ("await_decision",) and snapshot.values.get("approval_request_id") == str(request_id):
            resume_case(deps, checkpointer, case_id, payload)
            resumed.append(case_id)
    return resumed


# Nodes a run can be interrupted in (process killed, deploy, crash). Paused-for-approval threads are not
# interrupted: they wait for a decision and are resumed by the approval service.
RESUMABLE_NODES = {"start", "investigate", "assess_risk", "propose_resolution", "request_approval",
                   "execute_resolution", "failed"}


def recover_case(deps: WorkflowDeps, checkpointer: Any, case_id: uuid.UUID) -> WorkflowResult | None:
    """Finish a run that stopped mid-way, from its last PostgreSQL checkpoint (LangGraph durable execution).

    Completed steps are not repeated; the interrupted step runs again. Returns None when there is nothing to
    recover. The caller holds `case_run_lock`, so a run still in progress elsewhere is never driven twice.
    """
    graph = build_case_graph(deps, checkpointer)
    config = _run_config(None, case_id, "case_recovery")
    snapshot = graph.get_state(config)
    if not snapshot.next or not set(snapshot.next) <= RESUMABLE_NODES:
        return None
    log.info("workflow.recovering", case_id=case_id, next=list(snapshot.next))
    with log_context(case_id=case_id, workflow=WORKFLOW_VERSION):
        state = graph.invoke(None, config)
    return WorkflowResult(thread_id_for(case_id), bool(graph.get_state(config).next), dict(state))


def interrupted_cases(deps: WorkflowDeps, checkpointer: Any) -> list[uuid.UUID]:
    """Cases whose workflow stopped mid-way, across tenants (the recovery sweep runs as the system)."""
    with deps.unit_of_work() as session:
        bypass_rls(session)
        candidates = session.scalars(select(InvestigationCase.id).where(
            InvestigationCase.status == CaseStatus.INVESTIGATING,
            InvestigationCase.workflow_thread_id.like("case:%"))).all()  # Agent Server runs recover there
    graph = build_case_graph(deps, checkpointer)
    return [case_id for case_id in candidates
            if set(graph.get_state({"configurable": {"thread_id": thread_id_for(case_id)}}).next or ())
            & RESUMABLE_NODES]


# --- runs on the LangGraph Agent Server (Studio) ---


def _agent_server_client():
    from langgraph_sdk import get_sync_client

    from finrecon_ai.config import get_settings

    return get_sync_client(url=get_settings().agent_server_url)


def agent_server_thread_pending(thread_id: str) -> bool:
    """Whether an Agent Server thread is still running or waiting. Unreachable counts as pending: a case is
    never started twice because the server that owns it is down."""
    try:
        return bool(_agent_server_client().threads.get_state(thread_id).get("next"))
    except Exception:  # noqa: BLE001 (any failure means we cannot tell)
        log.warning("workflow.agent_server_unreachable", thread_id=thread_id)
        return True


def resume_on_agent_server(thread_id: str, decision: dict[str, Any]) -> WorkflowResult:
    from finrecon_ai.agent_server import CASE_GRAPH_ID

    client = _agent_server_client()
    values = client.runs.wait(thread_id, CASE_GRAPH_ID, command={"resume": decision})
    state = client.threads.get_state(thread_id)
    return WorkflowResult(thread_id, bool(state.get("next")), dict(values or {}))


def recover_interrupted(deps: WorkflowDeps, checkpointer_factory: Callable[[], AbstractContextManager[Any]],
                        run_lock: Callable[[uuid.UUID], AbstractContextManager[bool]] | None = None
                        ) -> list[uuid.UUID]:
    """The recovery sweep: finish every interrupted run that no process is driving. Used by
    `finrecon workflow recover` and at API startup."""
    run_lock = run_lock or case_run_lock
    recovered = []
    with checkpointer_factory() as checkpointer:
        for case_id in interrupted_cases(deps, checkpointer):
            with log_context(case_id=case_id), run_lock(case_id) as acquired:
                if not acquired:
                    log.info("workflow.recovery_skipped_running")
                    continue
                try:
                    if recover_case(deps, checkpointer, case_id) is not None:
                        recovered.append(case_id)
                except Exception:  # one bad case must not stop the sweep; its state shows the outcome
                    log.exception("workflow.recovery_failed")
    log.info("workflow.recovery_sweep", recovered=len(recovered))
    return recovered


def _run_config(tenant_id: uuid.UUID | None, case_id: uuid.UUID, run_name: str) -> dict[str, Any]:
    """Thread for checkpoints, plus the name, tags and metadata LangSmith shows for this run."""
    metadata = {"case_id": str(case_id), "workflow": WORKFLOW_VERSION}
    if tenant_id is not None:
        metadata["tenant_id"] = str(tenant_id)
    return {"configurable": {"thread_id": thread_id_for(case_id)}, "run_name": run_name,
            "tags": ["finrecon", WORKFLOW_VERSION], "metadata": metadata}


def workflow_state(deps: WorkflowDeps, checkpointer: Any, case_id: uuid.UUID) -> tuple[tuple[str, ...], dict]:
    snapshot = build_case_graph(deps, checkpointer).get_state({"configurable": {"thread_id": thread_id_for(case_id)}})
    return snapshot.next, dict(snapshot.values)


# --- nodes ---


def _start(deps: WorkflowDeps, state: CaseState, config: dict) -> dict[str, Any]:
    with deps.unit_of_work() as session:
        actor, case = _load(session, state)
        if case.status is CaseStatus.NEW:
            transition_case(session, actor, case.id, CaseStatus.INVESTIGATING)
        # Where this run lives: `case:<id>` here, or an Agent Server thread when started from Studio.
        case.workflow_thread_id = config["configurable"].get("thread_id") or thread_id_for(case.id)
        _audit(session, actor, case.id, "workflow.started", {"workflow": WORKFLOW_VERSION, "round": state["round"]})
    return {"error": None}


def _investigate(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    decision = state.get("decision") or {}
    brief = "Investigate this case."
    if decision:
        brief = ("A manager returned this case for further investigation. Their comment, as written: "
                 f"<reviewer_comment>{as_data(decision.get('comment') or '')}</reviewer_comment>. Earlier findings "
                 "are recorded on the case (get_case_findings); address the comment and investigate again.")
    return _run(deps, state, AgentName.INVESTIGATION, brief, lambda session, ctx, result: persist_findings(
        session, ctx, result.execution, result.output.evidence, result.output.findings), "investigation_execution_id")


def _assess_risk(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    return _run(deps, state, AgentName.RISK_ANALYSIS,
                "The investigation findings are recorded on this case. Assess the anomaly or duplicate indicators.",
                lambda session, ctx, result: persist_findings(session, ctx, result.execution,
                                                              result.output.evidence, result.output.indicators),
                "risk_execution_id")


def _propose(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    update = _run(deps, state, AgentName.RESOLUTION,
                  "The findings for this case are recorded. Propose one resolution.",
                  lambda session, ctx, result: persist_recommendation(session, ctx, result.execution, result.output),
                  "resolution_execution_id")
    if "persisted" in update:
        update["recommendation_id"] = str(update.pop("persisted").recommendation_id)
    return update


def _request_approval(deps: WorkflowDeps, state: CaseState, config: dict) -> dict[str, Any]:
    with deps.unit_of_work() as session:
        actor, case = _load(session, state)
        recommendation = session.get(ResolutionRecommendation, uuid.UUID(state["recommendation_id"]))
        recommendation.status = RecommendationStatus.PENDING_APPROVAL
        request = ApprovalRequest(
            tenant_id=case.tenant_id, case_id=case.id, recommendation_id=recommendation.id,
            required_role=RoleName.MANAGER, requested_by_agent_execution_id=recommendation.agent_execution_id,
            requested_by_user_id=actor.user_id,
            workflow_thread_id=config["configurable"]["thread_id"],
            workflow_checkpoint_id=config["configurable"].get("checkpoint_id"),
        )
        session.add(request)
        session.flush()
        transition_case(session, actor, case.id, CaseStatus.AWAITING_APPROVAL)
        _audit(session, actor, case.id, "approval.requested",
               {"approval_request_id": request.id, "recommendation_id": recommendation.id,
                "resolution_type": recommendation.resolution_type})
        log.info("workflow.paused_for_approval", approval_request_id=request.id)
        return {"approval_request_id": str(request.id)}


def _await_decision(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    # Pauses here until resumed. The resume value only says which decision to apply; the decision itself is read
    # back from the approval service's record, so a hand-made resume (an Agent Server client, Studio) cannot
    # approve, reject or return a case.
    resumed = interrupt({"approval_request_id": state["approval_request_id"], "case_id": state["case_id"],
                         "recommendation_id": state["recommendation_id"]})
    with deps.unit_of_work() as session:
        bind_tenant(session, uuid.UUID(state["tenant_id"]))
        decision = recorded_decision(session, state, resumed)
    return {"decision": decision, "round": state["round"] + (1 if _returned(decision) else 0)}


def recorded_decision(session: Session, state: CaseState, resumed: Any) -> dict[str, Any]:
    from finrecon_ai.approvals.service import resume_payload
    from finrecon_ai.db.models import ApprovalDecision

    decision_id = resumed.get("approval_decision_id") if isinstance(resumed, dict) else None
    try:
        row = session.get(ApprovalDecision, uuid.UUID(str(decision_id))) if decision_id else None
    except ValueError:
        row = None
    if row is None or str(row.approval_request_id) != state["approval_request_id"]:
        raise WorkflowError("resume value is not a decision recorded for this approval request")
    return resume_payload(row)


def _execute(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    from finrecon_ai.approvals.executor import execute_approved

    decision = state["decision"]
    with deps.unit_of_work() as session:
        approver = load_actor(session, uuid.UUID(state["tenant_id"]), uuid.UUID(decision["decided_by"]))
        result = execute_approved(session, approver, uuid.UUID(state["recommendation_id"]),
                                  uuid.UUID(decision["approval_decision_id"]))
    return {"execution_result": result}


def _failed(deps: WorkflowDeps, state: CaseState) -> dict[str, Any]:
    with deps.unit_of_work() as session:
        actor, case = _load(session, state)
        _audit(session, actor, case.id, "workflow.failed", {"error": state.get("error"), "round": state["round"]})
    log.warning("workflow.failed", error=state.get("error"))
    return {}


# --- routing ---


def _after_investigation(state: CaseState) -> str:
    if state.get("error"):
        return "failed"
    return "assess_risk" if state.get("category") in {c.value for c in RISK_CATEGORIES} else "propose_resolution"


def _after_decision(state: CaseState) -> str:
    decision = (state.get("decision") or {}).get("decision")
    if decision == ApprovalDecisionType.RETURN_FOR_INVESTIGATION.value:
        return "investigate"
    return "execute_resolution" if decision == ApprovalDecisionType.APPROVE.value else END


def _returned(decision: dict[str, Any] | None) -> bool:
    return bool(decision) and decision.get("decision") == ApprovalDecisionType.RETURN_FOR_INVESTIGATION.value


# --- helpers ---


def _run(deps: WorkflowDeps, state: CaseState, agent: AgentName, brief: str, persist, execution_key: str) -> dict:
    with deps.unit_of_work() as session:
        actor, case = _load(session, state)
        exception = session.get(ReconciliationException, case.exception_id)
        ctx = ToolContext(session=session, tenant_id=case.tenant_id, case=case, exception=exception,
                          policy_store=deps.policy_store)
        # The title is built from source IDs, which come from imported files: mark it as data.
        user_content = (f"Case {case.case_number} ({exception.category}, priority {case.priority}). "
                        f"<case_title>{as_data(case.title)}</case_title>\n\n{brief}")
        try:
            result = run_agent(session, spec=load_prompt(agent), ctx=ctx, user_content=user_content, client=deps.llm)
        except AgentFailed as exc:
            return {"error": f"{agent.value}: {exc}"}
        except Exception:
            session.commit()  # keep the failed execution record and its audit events, then let retry decide
            raise
        persisted = persist(session, ctx, result)
        update = {execution_key: str(result.execution.id), "error": None, "category": str(exception.category),
                  "rejected_output": [*state.get("rejected_output", []), *persisted.rejected]}
        if persisted.recommendation_id:
            update["persisted"] = persisted
        return update


def as_data(text: str) -> str:
    """Neutralise angle brackets so embedded text cannot close or open the tags that frame it as data."""
    return text.replace("<", "‹").replace(">", "›")


def _load(session: Session, state: CaseState):
    actor = load_actor(session, uuid.UUID(state["tenant_id"]), uuid.UUID(state["requested_by"]))
    return actor, get_case(session, actor, uuid.UUID(state["case_id"]))


def _audit(session: Session, actor: Actor, case_id: uuid.UUID, event_type: str, payload: dict) -> None:
    record_event(session, tenant_id=actor.tenant_id, event_type=event_type, entity_type="investigation_case",
                 entity_id=case_id, actor_type=ActorType.USER, actor_user_id=actor.user_id, payload=payload)


# --- runtime wiring ---


def session_unit_of_work() -> Callable[[], AbstractContextManager[Session]]:
    from finrecon_ai.db.session import new_session

    @contextmanager
    def unit() -> Iterator[Session]:
        with new_session() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return unit


CASE_RUN_LOCK = 3  # advisory-lock namespace (audit uses the single-key space, case numbering 2)


@contextmanager
def case_run_lock(case_id: uuid.UUID) -> Iterator[bool]:
    """Yields True if this process may run the case's workflow now. Held on its own connection for the whole run,
    so two API requests cannot drive the same thread at once."""
    import zlib

    from sqlalchemy import text

    from finrecon_ai.db.session import get_engine

    key = zlib.crc32(case_id.bytes) - 2**31
    with get_engine().connect() as connection:
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:ns, :key)"),
                                      {"ns": CASE_RUN_LOCK, "key": key}).scalar()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), {"ns": CASE_RUN_LOCK, "key": key})


def _checkpoint_url(database_url: str | None = None) -> str:
    from finrecon_ai.config import get_settings

    return (database_url or get_settings().database_url).replace("postgresql+psycopg://", "postgresql://")


def ensure_checkpoint_tables(database_url: str | None = None) -> None:
    """Create or migrate LangGraph's checkpoint tables (Alembic leaves them alone).

    Call before opening any other transaction: LangGraph builds its indexes CONCURRENTLY, which waits for every
    open transaction on the database to finish.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_checkpoint_url(database_url)) as saver:
        saver.setup()


@contextmanager
def postgres_checkpointer(database_url: str | None = None) -> Iterator[Any]:
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_checkpoint_url(database_url)) as saver:
        yield saver

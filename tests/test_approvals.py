"""Approval decisions, separation of duties, workflow resume and deterministic execution (FR-09, FR-10)."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from finrecon_ai.approvals.executor import ExecutionError, execute_approved
from finrecon_ai.approvals.service import ApprovalError, decide, list_pending
from finrecon_ai.authz import Actor, PermissionDenied, load_actor
from finrecon_ai.cases.service import InvalidTransition, transition_case
from finrecon_ai.db.enums import (
    ApprovalDecisionType,
    ApprovalRequestStatus,
    CaseStatus,
    ExceptionCategory,
    ExceptionStatus,
    MatchStrategy,
    RecommendationStatus,
    RoleName,
)
from finrecon_ai.db.models import (
    ApprovalDecision,
    ApprovalRequest,
    AuditEvent,
    ReconciliationException,
    ResolutionRecommendation,
    Role,
    TransactionMatch,
    User,
    transaction_match_items,
)
from finrecon_ai.workflows.case_investigation import decide_and_resume, investigate_case, resume_decided
from tests.fakes import AgentScripts, final, reply, tool_use
from tests.test_workflows import case_of, fee_case, investigation_steps, make_deps, store  # noqa: F401 (fixtures)

APPROVE, REJECT = ApprovalDecisionType.APPROVE, ApprovalDecisionType.REJECT
REQUEST_EVIDENCE = ApprovalDecisionType.REQUEST_EVIDENCE
RETURN = ApprovalDecisionType.RETURN_FOR_INVESTIGATION


def proposal_steps(resolution_type="request_documentation"):
    return [reply("tool_use", tool_use("get_case")), final({
        "resolution_type": resolution_type, "summary": "s", "rationale": "r", "action_steps": ["Contact PayGate"],
        "contact": "paygate", "evidence": [], "policy_labels": [], "confidence": "medium"})]


@pytest.fixture
def approver(session, world):
    """A second manager: the first one starts investigations, so they may not approve them."""
    tenant = world[0]
    user = User(tenant_id=tenant.id, email=f"approver-{uuid.uuid4().hex[:6]}@demo.dev", full_name="Approver",
                roles=[session.scalar(select(Role).where(Role.name == RoleName.MANAGER))])
    session.add(user)
    session.flush()
    return load_actor(session, tenant.id, user.id)


@pytest.fixture
def paused(session, world, make_deps):
    """Start a workflow as the demo manager on a case and leave it paused for approval."""
    saver = InMemorySaver()

    def start(case, resolution_type="request_documentation", llm=None):
        llm = llm or AgentScripts(investigation=investigation_steps(), resolution=proposal_steps(resolution_type))
        deps = make_deps(llm)
        result = investigate_case(deps, saver, world[2][RoleName.MANAGER], case.id)
        assert result.paused_for_approval
        return deps, saver, uuid.UUID(result.state["approval_request_id"])

    return start


def recommendation_of(session, request_id):
    return session.get(ResolutionRecommendation, session.get(ApprovalRequest, request_id).recommendation_id)


# --- the approval path ---


def test_approve_follow_up_resolution_executes_and_waits_for_evidence(session, fee_case, approver, paused):
    deps, saver, request_id = paused(fee_case)
    result = decide_and_resume(deps, saver, approver, request_id, APPROVE)

    assert not result.paused_for_approval
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_EVIDENCE
    rec = recommendation_of(session, request_id)
    assert rec.status is RecommendationStatus.EXECUTED and rec.executed_at is not None
    assert rec.execution_result["follow_up"]["steps"] == ["Contact PayGate"]
    assert rec.execution_idempotency_key == f"execute:{rec.id}"

    row = session.scalar(select(ApprovalDecision).where(ApprovalDecision.approval_request_id == request_id))
    assert row.reviewer_id == approver.user_id and row.decision is APPROVE
    assert row.original_recommendation["id"] == str(rec.id)
    assert row.final_action["execute"] == "request_documentation"
    assert session.get(ApprovalRequest, request_id).status is ApprovalRequestStatus.DECIDED

    events = session.scalars(select(AuditEvent.event_type).where(AuditEvent.tenant_id == fee_case.tenant_id)).all()
    assert "approval.decided" in events and "resolution.executed" in events


@pytest.mark.parametrize("resolution_type", ["confirm_fee_discrepancy", "no_action_required"])
def test_approve_resolving_type_resolves_case_and_exception(session, fee_case, approver, paused, resolution_type):
    deps, saver, request_id = paused(fee_case, resolution_type)
    decide_and_resume(deps, saver, approver, request_id, APPROVE)

    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.RESOLVED and fee_case.resolved_at is not None
    assert session.get(ReconciliationException, fee_case.exception_id).status is ExceptionStatus.RESOLVED


def test_manual_match_creates_approved_match(session, world, case_ids, approver, paused):
    case = case_of(session, world[0].id, ExceptionCategory.PARTIALLY_MATCHED)
    deps, saver, request_id = paused(case, "manual_match")
    decide_and_resume(deps, saver, approver, request_id, APPROVE)

    result = recommendation_of(session, request_id).execution_result
    match = session.get(TransactionMatch, uuid.UUID(result["match_id"]))
    assert match.strategy is MatchStrategy.MANUAL
    assert match.explanation["approved_by"] == str(approver.user_id)
    items = session.scalar(select(func.count()).select_from(transaction_match_items)
                           .where(transaction_match_items.c.match_id == match.id))
    assert items == 2


def test_manual_match_of_single_transaction_is_refused_before_recording(session, world, case_ids, approver,
                                                                         paused):
    case = case_of(session, world[0].id, ExceptionCategory.MISSING_SETTLEMENT)
    deps, saver, request_id = paused(case, "manual_match")
    with pytest.raises(ExecutionError, match="at least two transactions"):
        decide(session, approver, request_id, APPROVE)
    assert session.get(ApprovalRequest, request_id).status is ApprovalRequestStatus.PENDING


def test_reject(session, fee_case, approver, paused):
    deps, saver, request_id = paused(fee_case)
    result = decide_and_resume(deps, saver, approver, request_id, REJECT, comment="Fee is within contract.")
    assert not result.paused_for_approval and "execution_result" not in result.state
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.REJECTED
    assert recommendation_of(session, request_id).status is RecommendationStatus.REJECTED


def test_request_evidence(session, fee_case, approver, paused):
    deps, saver, request_id = paused(fee_case)
    decide_and_resume(deps, saver, approver, request_id, REQUEST_EVIDENCE, comment="Need the bank advice.")
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_EVIDENCE
    assert recommendation_of(session, request_id).status is RecommendationStatus.SUPERSEDED


def test_return_for_investigation_reruns_agents_and_asks_again(session, world, fee_case, approver, paused):
    llm = AgentScripts(investigation=investigation_steps() + investigation_steps(),
                       resolution=proposal_steps() + proposal_steps())
    deps, saver, first_request = paused(fee_case, llm=llm)
    result = decide_and_resume(deps, saver, approver, first_request, RETURN, comment="Check other fees.")

    assert result.paused_for_approval and result.state["round"] == 2
    second_request = session.get(ApprovalRequest, uuid.UUID(result.state["approval_request_id"]))
    assert second_request.id != first_request and second_request.status is ApprovalRequestStatus.PENDING
    assert second_request.requested_by_user_id == world[2][RoleName.MANAGER].user_id
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_APPROVAL


# --- rules ---


def test_requester_cannot_approve_own_investigation(session, world, fee_case, paused):
    _, _, request_id = paused(fee_case)
    with pytest.raises(PermissionDenied, match="separation of duties"):
        decide(session, world[2][RoleName.MANAGER], request_id, APPROVE)


@pytest.mark.parametrize("role", [RoleName.ANALYST, RoleName.ADMIN, RoleName.AUDITOR])
def test_only_deciding_roles(session, world, fee_case, paused, role):
    _, _, request_id = paused(fee_case)
    with pytest.raises(PermissionDenied):
        decide(session, world[2][role], request_id, APPROVE)


def test_system_actor_cannot_decide(session, world, fee_case, paused):
    _, _, request_id = paused(fee_case)
    with pytest.raises(PermissionDenied):
        decide(session, Actor.system(world[0].id), request_id, APPROVE)


@pytest.mark.parametrize("decision", [REJECT, REQUEST_EVIDENCE, RETURN])
def test_non_approval_needs_comment(session, fee_case, approver, paused, decision):
    _, _, request_id = paused(fee_case)
    with pytest.raises(ApprovalError, match="needs a comment"):
        decide(session, approver, request_id, decision, comment="  ")


def test_one_decision_per_request(session, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    row = decide(session, approver, request_id, REJECT, comment="no")
    with pytest.raises(ApprovalError, match="already decided"):
        decide(session, approver, request_id, APPROVE)

    duplicate = ApprovalDecision(tenant_id=row.tenant_id, approval_request_id=request_id, reviewer_id=row.reviewer_id,
                                 decision=APPROVE, original_recommendation={})
    session.add(duplicate)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.flush()


def test_decisions_are_immutable(session, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    decide(session, approver, request_id, REJECT, comment="no")
    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.execute(update(ApprovalDecision).values(decision=APPROVE))


def test_evidence_considered_must_belong_to_case(session, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    with pytest.raises(ApprovalError, match="belong to this case"):
        decide(session, approver, request_id, APPROVE, evidence_considered_ids=[uuid.uuid4()])


def test_other_tenant_cannot_see_request(session, fee_case, paused):
    _, _, request_id = paused(fee_case)
    stranger = Actor(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), roles=frozenset({RoleName.MANAGER}))
    with pytest.raises(ApprovalError, match="not found"):
        decide(session, stranger, request_id, APPROVE)


def test_pending_list(session, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    assert [r.id for r in list_pending(session, approver)] == [request_id]
    with pytest.raises(PermissionDenied):
        list_pending(session, Actor(tenant_id=approver.tenant_id, user_id=uuid.uuid4(),
                                    roles=frozenset({RoleName.ANALYST})))


def test_resolved_only_by_execution(session, world, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    decide(session, approver, request_id, APPROVE)  # recorded but not executed yet
    with pytest.raises(InvalidTransition, match="executing an approved resolution"):
        transition_case(session, world[2][RoleName.MANAGER], fee_case.id, CaseStatus.RESOLVED)


# --- execution safety and recovery ---


def test_execution_is_idempotent(session, fee_case, approver, paused):
    deps, saver, request_id = paused(fee_case, "confirm_fee_discrepancy")
    decide_and_resume(deps, saver, approver, request_id, APPROVE)
    rec = recommendation_of(session, request_id)
    decision_id = uuid.UUID(rec.execution_result["approval_decision_id"])
    events_before = session.scalar(select(func.count()).select_from(AuditEvent))

    again = execute_approved(session, approver, rec.id, decision_id)
    assert again == rec.execution_result
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == events_before


def test_execution_needs_an_approving_decision(session, fee_case, approver, paused):
    _, _, request_id = paused(fee_case)
    rec = recommendation_of(session, request_id)
    with pytest.raises(ExecutionError, match="not approved"):
        execute_approved(session, approver, rec.id, uuid.uuid4())


def test_recorded_decision_resumes_after_interruption(session, world, fee_case, approver, paused):
    deps, saver, request_id = paused(fee_case, "confirm_fee_discrepancy")
    decide(session, approver, request_id, APPROVE)  # crash here: decision saved, workflow never resumed

    assert resume_decided(deps, saver, world[0].id) == [fee_case.id]
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.RESOLVED
    assert resume_decided(deps, saver, world[0].id) == [], "nothing left to resume"

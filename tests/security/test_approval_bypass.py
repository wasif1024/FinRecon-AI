"""Attempts to get a resolution approved or executed without a genuine, valid human decision."""

import uuid

import pytest
from sqlalchemy import select

from finrecon_ai.approvals.executor import ExecutionError, execute_approved
from finrecon_ai.approvals.service import ApprovalError, decide
from finrecon_ai.authz import Actor, PermissionDenied
from finrecon_ai.cases.service import InvalidTransition, controlled_transition, transition_case
from finrecon_ai.db.enums import (
    ApprovalDecisionType,
    CaseStatus,
    RecommendationStatus,
    RoleName,
)
from finrecon_ai.db.models import ApprovalRequest, InvestigationCase, ResolutionRecommendation, Role, User
from finrecon_ai.workflows.case_investigation import WorkflowError, decide_and_resume, resume_case
from tests.test_api import auth, client, scripts  # noqa: F401 (fixtures)
from tests.test_approvals import approver, paused, proposal_steps  # noqa: F401 (fixtures)
from tests.test_workflows import fee_case, make_deps, store  # noqa: F401 (fixtures)

APPROVE = ApprovalDecisionType.APPROVE


def rec_of(session, request_id):
    return session.get(ResolutionRecommendation, session.get(ApprovalRequest, request_id).recommendation_id)


@pytest.mark.parametrize("target", [CaseStatus.APPROVED, CaseStatus.REJECTED, CaseStatus.RESOLVED,
                                    CaseStatus.CLOSED, CaseStatus.INVESTIGATING])
def test_no_role_can_move_an_awaiting_case_directly(session, world, fee_case, paused, target):
    paused(fee_case)
    for role in RoleName:
        with pytest.raises((InvalidTransition, PermissionDenied)):
            transition_case(session, world[2][role], fee_case.id, target, comment="x")
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_APPROVAL


def test_controlled_transition_rejects_the_wrong_channel(session, world, fee_case, paused, approver):
    paused(fee_case)
    with pytest.raises(InvalidTransition):  # executor channel cannot make an approval move
        controlled_transition(session, approver, fee_case, CaseStatus.APPROVED, via="executor", reference={})
    with pytest.raises(InvalidTransition):  # nor jump straight to resolved
        controlled_transition(session, approver, fee_case, CaseStatus.RESOLVED, via="approval", reference={})


def test_forged_resume_cannot_execute(session, world, fee_case, paused):
    """Resuming the paused graph with an 'approve' that has no decision behind it."""
    deps, saver, request_id = paused(fee_case, "confirm_fee_discrepancy")
    forged = {"decision": "approve", "comment": None, "approval_decision_id": str(uuid.uuid4()),
              "decided_by": str(world[2][RoleName.MANAGER].user_id)}
    with pytest.raises(ExecutionError):
        resume_case(deps, saver, fee_case.id, forged)
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_APPROVAL
    assert rec_of(session, request_id).status is RecommendationStatus.PENDING_APPROVAL


def test_execute_with_a_decision_from_another_recommendation(session, world, fee_case, paused, approver):
    from tests.fakes import AgentScripts
    from tests.test_workflows import investigation_steps

    llm = AgentScripts(investigation=investigation_steps() * 2, resolution=proposal_steps() + proposal_steps())
    deps, saver, first_request = paused(fee_case, llm=llm)
    first = decide_and_resume(deps, saver, approver, first_request, ApprovalDecisionType.RETURN_FOR_INVESTIGATION,
                              comment="again")
    old_decision = session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == first_request))
    new_rec = rec_of(session, uuid.UUID(first.state["approval_request_id"]))
    new_rec.status = RecommendationStatus.APPROVED  # simulate a bug that marked it approved
    session.flush()
    from finrecon_ai.db.models import ApprovalDecision

    decision = session.scalar(select(ApprovalDecision).where(ApprovalDecision.approval_request_id == old_decision.id))
    with pytest.raises(ExecutionError, match="no approval decision authorises"):
        execute_approved(session, approver, new_rec.id, decision.id)


def test_executor_refuses_unapproved_recommendation(session, fee_case, paused, approver):
    _, _, request_id = paused(fee_case)
    with pytest.raises(ExecutionError, match="not approved"):
        execute_approved(session, approver, rec_of(session, request_id).id, uuid.uuid4())


def test_replaying_a_resume_after_completion(session, fee_case, paused, approver):
    deps, saver, request_id = paused(fee_case, "confirm_fee_discrepancy")
    decide_and_resume(deps, saver, approver, request_id, APPROVE)
    with pytest.raises(WorkflowError, match="not waiting"):
        resume_case(deps, saver, fee_case.id, {"decision": "approve", "approval_decision_id": str(uuid.uuid4()),
                                               "decided_by": str(approver.user_id)})


def test_superseded_request_cannot_be_decided(session, world, fee_case, paused, approver):
    from tests.fakes import AgentScripts
    from tests.test_workflows import investigation_steps

    llm = AgentScripts(investigation=investigation_steps() * 2, resolution=proposal_steps() + proposal_steps())
    deps, saver, first_request = paused(fee_case, llm=llm)
    decide_and_resume(deps, saver, approver, first_request, ApprovalDecisionType.RETURN_FOR_INVESTIGATION,
                      comment="again")
    with pytest.raises(ApprovalError, match="already"):
        decide(session, approver, first_request, APPROVE)


def test_manager_with_extra_roles_still_cannot_self_approve(session, world, fee_case, paused):
    _, _, request_id = paused(fee_case)
    manager_user = session.get(User, world[2][RoleName.MANAGER].user_id)
    manager_user.roles = [*manager_user.roles, *session.scalars(select(Role).where(Role.name != RoleName.MANAGER))]
    session.flush()
    from finrecon_ai.authz import load_actor

    all_roles = load_actor(session, world[0].id, manager_user.id)
    with pytest.raises(PermissionDenied, match="separation of duties"):
        decide(session, all_roles, request_id, APPROVE)


def test_fabricated_actor_from_another_tenant(session, fee_case, paused):
    _, _, request_id = paused(fee_case)
    outsider = Actor(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), roles=frozenset({RoleName.MANAGER}))
    with pytest.raises(ApprovalError, match="not found"):
        decide(session, outsider, request_id, APPROVE)


# --- over HTTP ---


def test_api_rejects_direct_resolution_and_self_approval(session, world, client, scripts, case_ids):
    from tests.fakes import AgentScripts
    from tests.test_workflows import case_of, investigation_steps
    from finrecon_ai.db.enums import ExceptionCategory

    case = case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)
    scripts.llm = AgentScripts(investigation=investigation_steps(), resolution=proposal_steps())
    manager = auth(client, RoleName.MANAGER)
    client.post(f"/cases/{case.id}/investigate", headers=manager)
    request_id = client.get(f"/cases/{case.id}/workflow", headers=manager).json()["approval_request_id"]

    for target in ("approved", "resolved"):
        assert client.post(f"/cases/{case.id}/transition", json={"to_status": target, "comment": "x"},
                           headers=manager).status_code == 409
    assert client.post(f"/approvals/{request_id}/decision", json={"decision": "approve"},
                       headers=manager).status_code == 403
    assert client.post(f"/approvals/{request_id}/decision", json={"decision": "approve"},
                       headers=auth(client, RoleName.ANALYST)).status_code == 403
    # Smuggling a reviewer or decision record through the body is refused by the schema.
    assert client.post(f"/approvals/{request_id}/decision",
                       json={"decision": "approve", "reviewer_id": str(uuid.uuid4())},
                       headers=manager).status_code == 422
    session.refresh(case)
    assert case.status is CaseStatus.AWAITING_APPROVAL
    assert session.get(InvestigationCase, case.id).resolved_at is None

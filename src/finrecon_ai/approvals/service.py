"""Human approval of recommended resolutions (FR-09).

A decision is recorded first (its own transaction), then the paused workflow is resumed with it. If resuming
fails, the decision is safe and `resume_decided` picks it up again. Rules:
- only roles with CASE_DECIDE may decide, and only on a pending request in their tenant;
- whoever started the investigation that produced the recommendation may not decide it;
- exactly one decision per request (database constraint) and decisions are immutable (database trigger);
- every decision except approve needs a comment;
- an approval is accepted only if the executor can carry it out.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.approvals.executor import EXECUTION_PLANS, check_executable
from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor, Permission, PermissionDenied
from finrecon_ai.cases.service import controlled_transition, get_case
from finrecon_ai.db.enums import (
    ActorType,
    ApprovalDecisionType,
    ApprovalRequestStatus,
    CaseStatus,
    RecommendationStatus,
)
from finrecon_ai.db.models import ApprovalDecision, ApprovalRequest, InvestigationEvidence, ResolutionRecommendation
from finrecon_ai.errors import NotFound
from finrecon_ai.log import get_logger

log = get_logger(__name__)

CASE_STATUS_AFTER = {
    ApprovalDecisionType.APPROVE: CaseStatus.APPROVED,
    ApprovalDecisionType.REJECT: CaseStatus.REJECTED,
    ApprovalDecisionType.REQUEST_EVIDENCE: CaseStatus.AWAITING_EVIDENCE,
    ApprovalDecisionType.RETURN_FOR_INVESTIGATION: CaseStatus.INVESTIGATING,
}
RECOMMENDATION_STATUS_AFTER = {
    ApprovalDecisionType.APPROVE: RecommendationStatus.APPROVED,
    ApprovalDecisionType.REJECT: RecommendationStatus.REJECTED,
    # Not accepted yet; a fresh recommendation follows once evidence arrives or investigation reruns.
    ApprovalDecisionType.REQUEST_EVIDENCE: RecommendationStatus.SUPERSEDED,
    ApprovalDecisionType.RETURN_FOR_INVESTIGATION: RecommendationStatus.SUPERSEDED,
}


class ApprovalError(Exception):
    pass


class ApprovalNotFound(NotFound, ApprovalError):
    pass


def list_pending(session: Session, actor: Actor) -> list[ApprovalRequest]:
    actor.require(Permission.CASE_DECIDE)
    return list(session.scalars(select(ApprovalRequest)
                                .where(ApprovalRequest.tenant_id == actor.tenant_id,
                                       ApprovalRequest.status == ApprovalRequestStatus.PENDING)
                                .order_by(ApprovalRequest.created_at)))


def decide(session: Session, actor: Actor, request_id: uuid.UUID, decision: ApprovalDecisionType, *,
           comment: str | None = None, evidence_considered_ids: list[uuid.UUID] | None = None) -> ApprovalDecision:
    actor.require(Permission.CASE_DECIDE)
    request = session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == request_id,
                                                           ApprovalRequest.tenant_id == actor.tenant_id)
                             .with_for_update())
    if request is None:
        raise ApprovalNotFound("approval request not found")
    if request.status is not ApprovalRequestStatus.PENDING:
        raise ApprovalError(f"approval request is already {request.status}")
    if request.required_role not in actor.roles:
        raise PermissionDenied(f"this decision requires the {request.required_role} role")
    if request.requested_by_user_id is not None and request.requested_by_user_id == actor.user_id:
        raise PermissionDenied("separation of duties: you started the investigation behind this recommendation")

    comment = (comment or "").strip() or None
    if decision is not ApprovalDecisionType.APPROVE and comment is None:
        raise ApprovalError(f"a {decision} decision needs a comment explaining it")

    case = get_case(session, actor, request.case_id, for_update=True)
    if case.status is not CaseStatus.AWAITING_APPROVAL:
        raise ApprovalError(f"case is {case.status}, not awaiting approval")
    recommendation = session.get(ResolutionRecommendation, request.recommendation_id)
    if recommendation.status is not RecommendationStatus.PENDING_APPROVAL:
        raise ApprovalError(f"recommendation is {recommendation.status}")
    if decision is ApprovalDecisionType.APPROVE:
        check_executable(session, case, recommendation)

    case_evidence = set(session.scalars(select(InvestigationEvidence.id)
                                        .where(InvestigationEvidence.case_id == case.id)))
    considered = list(evidence_considered_ids) if evidence_considered_ids is not None else sorted(case_evidence, key=str)
    if not set(considered) <= case_evidence:
        raise ApprovalError("evidence_considered_ids must belong to this case")

    row = ApprovalDecision(
        tenant_id=actor.tenant_id, approval_request_id=request.id, reviewer_id=actor.user_id, decision=decision,
        comments=comment, evidence_considered_ids=considered,
        original_recommendation=_snapshot(recommendation),
        final_action=({"execute": str(recommendation.resolution_type),
                       "plan": EXECUTION_PLANS[recommendation.resolution_type]}
                      if decision is ApprovalDecisionType.APPROVE else None),
    )
    session.add(row)
    request.status = ApprovalRequestStatus.DECIDED
    recommendation.status = RECOMMENDATION_STATUS_AFTER[decision]
    session.flush()
    controlled_transition(session, actor, case, CASE_STATUS_AFTER[decision], via="approval",
                          reference={"approval_decision_id": row.id, "decision": decision})

    log.info("approval.decided", approval_request_id=request.id, decision=decision, case_id=case.id)
    record_event(session, tenant_id=actor.tenant_id, event_type="approval.decided", entity_type="approval_request",
                 entity_id=request.id, actor_type=ActorType.USER, actor_user_id=actor.user_id,
                 payload={"decision": decision, "approval_decision_id": row.id, "case_id": case.id,
                          "recommendation_id": recommendation.id, "comment": comment,
                          "evidence_considered": len(considered)})
    return row


def resume_payload(row: ApprovalDecision) -> dict[str, Any]:
    """What the paused workflow receives on resume."""
    return {"decision": row.decision.value, "comment": row.comments, "approval_decision_id": str(row.id),
            "decided_by": str(row.reviewer_id)}


def _snapshot(recommendation: ResolutionRecommendation) -> dict[str, Any]:
    return {"id": str(recommendation.id), "version": recommendation.version,
            "resolution_type": str(recommendation.resolution_type), "summary": recommendation.summary,
            "rationale": recommendation.rationale, "proposed_action": recommendation.proposed_action,
            "confidence": str(recommendation.confidence) if recommendation.confidence is not None else None,
            "agent_execution_id": str(recommendation.agent_execution_id)}

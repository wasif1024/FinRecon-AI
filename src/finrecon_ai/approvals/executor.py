"""Carry out an approved resolution through fixed, audited steps (FR-10).

Nothing here moves money or changes an external ledger (SRS §4.2); executing a resolution means recording its
outcome in FinRecon and moving the case on. Each resolution type maps to one plan. Execution is idempotent: a
recommendation executes at most once (unique execution key), and a retry returns the recorded result.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor
from finrecon_ai.cases.service import controlled_transition
from finrecon_ai.db.enums import (
    ActorType,
    ApprovalDecisionType,
    CaseStatus,
    ExceptionStatus,
    MatchClassification,
    MatchStrategy,
    RecommendationStatus,
    ResolutionType,
)
from finrecon_ai.db.models import (
    ApprovalDecision,
    ApprovalRequest,
    InvestigationCase,
    ReconciliationException,
    ResolutionRecommendation,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)
from finrecon_ai.log import get_logger

log = get_logger(__name__)

# What approving each type will do; shown to the reviewer and stored on the decision.
EXECUTION_PLANS: dict[ResolutionType, str] = {
    ResolutionType.CONFIRM_FEE_DISCREPANCY: "record the fee discrepancy as confirmed; resolve the exception and case",
    ResolutionType.NO_ACTION_REQUIRED: "record that no correction is needed; resolve the exception and case",
    ResolutionType.MANUAL_MATCH: "create an approved manual match between the case's transactions; resolve the "
                                 "exception and case",
    ResolutionType.REQUEST_DOCUMENTATION: "record the follow-up steps; case waits for evidence",
    ResolutionType.INVESTIGATE_DUPLICATE: "record the follow-up steps; case waits for evidence",
    ResolutionType.ESCALATE: "record the escalation steps; case waits for the escalation outcome",
}
RESOLVING = {ResolutionType.CONFIRM_FEE_DISCREPANCY, ResolutionType.NO_ACTION_REQUIRED, ResolutionType.MANUAL_MATCH}


class ExecutionError(Exception):
    pass


def check_executable(session: Session, case: InvestigationCase, recommendation: ResolutionRecommendation) -> None:
    """Refuse an approval up front if its plan could not be carried out."""
    if recommendation.resolution_type is ResolutionType.MANUAL_MATCH:
        count = len(_exception_transactions(session, case.exception_id))
        if count < 2:
            raise ExecutionError(f"a manual match needs at least two transactions; this case has {count}")


def execute_approved(session: Session, actor: Actor, recommendation_id: uuid.UUID,
                     approval_decision_id: uuid.UUID) -> dict[str, Any]:
    recommendation = session.scalar(select(ResolutionRecommendation)
                                    .where(ResolutionRecommendation.id == recommendation_id,
                                           ResolutionRecommendation.tenant_id == actor.tenant_id)
                                    .with_for_update())
    if recommendation is None:
        raise ExecutionError("recommendation not found")
    if recommendation.status is RecommendationStatus.EXECUTED:
        log.info("resolution.already_executed", recommendation_id=recommendation.id)
        return recommendation.execution_result or {}
    if recommendation.status is not RecommendationStatus.APPROVED:
        raise ExecutionError(f"recommendation is {recommendation.status}, not approved")

    decision = session.scalar(
        select(ApprovalDecision).join(ApprovalRequest, ApprovalRequest.id == ApprovalDecision.approval_request_id)
        .where(ApprovalDecision.id == approval_decision_id, ApprovalRequest.recommendation_id == recommendation.id))
    if decision is None or decision.decision is not ApprovalDecisionType.APPROVE:
        raise ExecutionError("no approval decision authorises this recommendation")

    case = session.get(InvestigationCase, recommendation.case_id)
    exception = session.get(ReconciliationException, case.exception_id)
    check_executable(session, case, recommendation)
    resolution = recommendation.resolution_type
    result: dict[str, Any] = {"resolution_type": str(resolution), "approval_decision_id": str(decision.id),
                              "amount": _str(case.discrepancy_amount), "currency": case.currency}

    if resolution is ResolutionType.MANUAL_MATCH:
        result["match_id"] = str(_manual_match(session, actor, case, exception, recommendation, decision))
    if resolution in RESOLVING:
        exception.status = ExceptionStatus.RESOLVED
        result["case_status"] = CaseStatus.RESOLVED.value
    else:
        result["follow_up"] = {"contact": recommendation.proposed_action.get("contact"),
                               "steps": recommendation.proposed_action.get("action_steps", [])}
        result["case_status"] = CaseStatus.AWAITING_EVIDENCE.value

    recommendation.execution_idempotency_key = f"execute:{recommendation.id}"
    recommendation.status = RecommendationStatus.EXECUTED
    recommendation.executed_at = datetime.now(UTC)
    recommendation.execution_result = result
    session.flush()  # the unique execution key rejects a concurrent second execution here
    controlled_transition(session, actor, case, CaseStatus(result["case_status"]), via="executor",
                          reference={"recommendation_id": recommendation.id, "approval_decision_id": decision.id})

    log.info("resolution.executed", recommendation_id=recommendation.id, resolution_type=resolution,
             case_status=result["case_status"])
    record_event(session, tenant_id=actor.tenant_id, event_type="resolution.executed",
                 entity_type="resolution_recommendation", entity_id=recommendation.id, actor_type=ActorType.USER,
                 actor_user_id=actor.user_id, payload=result)
    return result


def _manual_match(session: Session, actor: Actor, case: InvestigationCase, exception: ReconciliationException,
                  recommendation: ResolutionRecommendation, decision: ApprovalDecision) -> uuid.UUID:
    match_id = uuid.uuid4()
    txn_ids = _exception_transactions(session, exception.id)
    session.execute(insert(TransactionMatch).values(
        id=match_id, tenant_id=actor.tenant_id, run_id=exception.run_id, classification=MatchClassification.MATCHED,
        strategy=MatchStrategy.MANUAL, score=Decimal("1"), amount_difference=None, currency=case.currency,
        explanation={"summary": "manual match approved by a reviewer", "case_id": str(case.id),
                     "recommendation_id": str(recommendation.id), "approval_decision_id": str(decision.id),
                     "approved_by": str(decision.reviewer_id), "rationale": recommendation.rationale}))
    session.execute(insert(transaction_match_items),
                    [{"match_id": match_id, "transaction_id": txn_id} for txn_id in txn_ids])
    return match_id


def _exception_transactions(session: Session, exception_id: uuid.UUID) -> list[uuid.UUID]:
    return list(session.scalars(select(exception_transactions.c.transaction_id)
                                .where(exception_transactions.c.exception_id == exception_id)))


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)

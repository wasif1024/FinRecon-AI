import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import Runtime, current_actor, get_session, runtime
from finrecon_ai.api.schemas import ApprovalRequestOut, DecisionIn, DecisionOut, PendingApprovalOut, RecommendationOut
from finrecon_ai.approvals.service import decide, list_pending, resume_payload
from finrecon_ai.authz import Actor
from finrecon_ai.db.models import ApprovalRequest, InvestigationCase, ResolutionRecommendation
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.workflows.case_investigation import resume_case

log = get_logger(__name__)
router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[PendingApprovalOut])
def pending(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    result = []
    for request in list_pending(session, actor):
        case = session.get(InvestigationCase, request.case_id)
        result.append(PendingApprovalOut(
            request=ApprovalRequestOut.model_validate(request), case_number=case.case_number, case_title=case.title,
            recommendation=RecommendationOut.model_validate(session.get(ResolutionRecommendation,
                                                                        request.recommendation_id))))
    return result


@router.post("/{request_id}/decision", response_model=DecisionOut, status_code=status.HTTP_202_ACCEPTED)
def decide_request(request_id: uuid.UUID, body: DecisionIn, background: BackgroundTasks,
                   actor: Actor = Depends(current_actor), session: Session = Depends(get_session),
                   rt: Runtime = Depends(runtime)):
    """Records the decision now; the paused workflow resumes in the background (poll the case's workflow).
    If the resume does not complete, `finrecon approvals resume` picks it up."""
    row = decide(session, actor, request_id, body.decision, comment=body.comment,
                 evidence_considered_ids=body.evidence_considered_ids)
    case_id = session.get(ApprovalRequest, request_id).case_id
    session.commit()  # the decision must be durable before the workflow continues
    background.add_task(_resume, rt, case_id, resume_payload(row))
    return row


def _resume(rt: Runtime, case_id: uuid.UUID, payload: dict) -> None:
    with log_context(case_id=case_id), rt.run_lock(case_id) as acquired:
        if not acquired:
            log.warning("workflow.resume_skipped_already_running")
            return
        try:
            with rt.checkpointer() as checkpointer:
                resume_case(rt.workflow_deps(), checkpointer, case_id, payload)
        except Exception:
            log.exception("workflow.background_resume_failed")

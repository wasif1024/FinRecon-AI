import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import Runtime, current_actor, get_session, runtime
from finrecon_ai.api.schemas import (
    AssignIn,
    AuditEventOut,
    CaseDetailOut,
    CaseListOut,
    CaseSummaryOut,
    EvidenceOut,
    FindingOut,
    MatchOut,
    NoteIn,
    TransactionOut,
    TransitionIn,
    WorkflowOut,
)
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.cases.service import add_note, assign_case, get_case, require_assignment, transition_case
from finrecon_ai.db.enums import CaseStatus, Severity
from finrecon_ai.db.models import (
    AgentExecutionRecord,
    ApprovalDecision,
    ApprovalRequest,
    AuditEvent,
    FinancialSource,
    InvestigationCase,
    InvestigationEvidence,
    InvestigationFinding,
    ReconciliationException,
    ResolutionRecommendation,
    Transaction,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.workflows.case_investigation import STARTABLE, WorkflowError, investigate_case, workflow_state

log = get_logger(__name__)
router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("", response_model=CaseListOut)
def list_cases(status_filter: CaseStatus | None = Query(None, alias="status"), priority: Severity | None = None,
               assigned_to: uuid.UUID | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.CASE_READ)
    query = select(InvestigationCase).where(InvestigationCase.tenant_id == actor.tenant_id)
    if status_filter:
        query = query.where(InvestigationCase.status == status_filter)
    if priority:
        query = query.where(InvestigationCase.priority == priority)
    if assigned_to:
        query = query.where(InvestigationCase.assigned_to == assigned_to)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    items = session.scalars(query.order_by(InvestigationCase.case_number).limit(limit).offset(offset)).all()
    return CaseListOut(total=total, items=[CaseSummaryOut.model_validate(c) for c in items])


@router.get("/{case_id}", response_model=CaseDetailOut)
def case_detail(case_id: uuid.UUID, actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    case = get_case(session, actor, case_id)
    exception = session.get(ReconciliationException, case.exception_id)
    txn_rows = session.execute(
        select(Transaction, FinancialSource.source_type)
        .join(FinancialSource, FinancialSource.id == Transaction.source_id)
        .join(exception_transactions, exception_transactions.c.transaction_id == Transaction.id)
        .where(exception_transactions.c.exception_id == exception.id).order_by(Transaction.transaction_date)
    ).all()
    txn_ids = [t.id for t, _ in txn_rows]
    matches = session.scalars(
        select(TransactionMatch).where(TransactionMatch.run_id == exception.run_id, TransactionMatch.id.in_(
            select(transaction_match_items.c.match_id).where(transaction_match_items.c.transaction_id.in_(txn_ids))))
    ).all()
    findings = session.execute(
        select(InvestigationFinding, AgentExecutionRecord.agent_name)
        .outerjoin(AgentExecutionRecord, AgentExecutionRecord.id == InvestigationFinding.agent_execution_id)
        .where(InvestigationFinding.case_id == case.id).order_by(InvestigationFinding.created_at)
    ).all()
    requests = session.scalars(select(ApprovalRequest).where(ApprovalRequest.case_id == case.id)
                               .order_by(ApprovalRequest.created_at)).all()
    return CaseDetailOut.model_validate({
        **CaseSummaryOut.model_validate(case).model_dump(),
        "closure_reason": case.closure_reason, "resolved_at": case.resolved_at, "closed_at": case.closed_at,
        "exception": exception,
        "transactions": [TransactionOut.model_validate(t).model_copy(update={"source_type": st}) for t, st in txn_rows],
        "matches": [MatchOut.model_validate(m) for m in matches],
        "findings": [FindingOut.model_validate(f).model_copy(update={"recorded_by": agent or "analyst"})
                     for f, agent in findings],
        "evidence": [EvidenceOut.model_validate(e) for e in session.scalars(
            select(InvestigationEvidence).where(InvestigationEvidence.case_id == case.id)
            .order_by(InvestigationEvidence.created_at))],
        "recommendations": session.scalars(select(ResolutionRecommendation)
                                           .where(ResolutionRecommendation.case_id == case.id)
                                           .order_by(ResolutionRecommendation.version)).all(),
        "approval_requests": requests,
        "decisions": session.scalars(select(ApprovalDecision).where(
            ApprovalDecision.approval_request_id.in_([r.id for r in requests]))).all(),
    })


@router.post("/{case_id}/assign", response_model=CaseSummaryOut)
def assign(case_id: uuid.UUID, body: AssignIn, actor: Actor = Depends(current_actor),
           session: Session = Depends(get_session)):
    return assign_case(session, actor, case_id, body.assignee_id)


@router.post("/{case_id}/transition", response_model=CaseSummaryOut)
def transition(case_id: uuid.UUID, body: TransitionIn, actor: Actor = Depends(current_actor),
               session: Session = Depends(get_session)):
    return transition_case(session, actor, case_id, body.to_status, comment=body.comment,
                           closure_reason=body.closure_reason)


@router.post("/{case_id}/notes", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
def note(case_id: uuid.UUID, body: NoteIn, actor: Actor = Depends(current_actor),
         session: Session = Depends(get_session)):
    return add_note(session, actor, case_id, body.text)


@router.post("/{case_id}/investigate", response_model=WorkflowOut, status_code=status.HTTP_202_ACCEPTED)
def investigate(case_id: uuid.UUID, background: BackgroundTasks, actor: Actor = Depends(current_actor),
                session: Session = Depends(get_session), rt: Runtime = Depends(runtime)):
    """Starts the agent workflow in the background (about a minute); poll GET /cases/{id}/workflow."""
    actor.require(Permission.CASE_WORK)
    case = get_case(session, actor, case_id)
    require_assignment(actor, case)
    if case.status not in STARTABLE:
        raise WorkflowError(f"case is {case.status}; investigation can start only from new or investigating")
    with rt.checkpointer() as checkpointer:
        pending, _ = workflow_state(rt.workflow_deps(), checkpointer, case_id)
    if pending:
        raise WorkflowError("this case's workflow is already running or waiting for a decision")
    background.add_task(_run_investigation, rt, actor, case_id)
    return WorkflowOut(case_id=case_id, started=True, paused_for_approval=False, running=True)


@router.get("/{case_id}/workflow", response_model=WorkflowOut)
def workflow(case_id: uuid.UUID, actor: Actor = Depends(current_actor), session: Session = Depends(get_session),
             rt: Runtime = Depends(runtime)):
    get_case(session, actor, case_id)  # permission and tenant check
    with rt.checkpointer() as checkpointer:
        pending, values = workflow_state(rt.workflow_deps(), checkpointer, case_id)
    paused = pending == ("await_decision",)
    return WorkflowOut(case_id=case_id, started=bool(values), paused_for_approval=paused,
                       running=bool(pending) and not paused, round=values.get("round"),
                       approval_request_id=values.get("approval_request_id"),
                       recommendation_id=values.get("recommendation_id"), error=values.get("error"),
                       rejected_output=values.get("rejected_output", []),
                       execution_result=values.get("execution_result"))


@router.get("/{case_id}/audit", response_model=list[AuditEventOut])
def case_audit(case_id: uuid.UUID, actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.AUDIT_READ)
    get_case(session, actor, case_id)
    return session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == actor.tenant_id,
                                                    AuditEvent.entity_id == str(case_id)).order_by(AuditEvent.id)).all()


def _run_investigation(rt: Runtime, actor: Actor, case_id: uuid.UUID) -> None:
    with log_context(case_id=case_id), rt.run_lock(case_id) as acquired:
        if not acquired:
            log.warning("workflow.skipped_already_running")
            return
        try:
            with rt.checkpointer() as checkpointer:
                investigate_case(rt.workflow_deps(), checkpointer, actor, case_id)
        except Exception:  # background: nobody to return an error to; the workflow state shows the outcome
            log.exception("workflow.background_failed")

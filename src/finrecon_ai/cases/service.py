"""Investigation case management (FR-05): create cases from exceptions, assign, move through the lifecycle.

Every function takes an Actor, checks permissions and tenant, and writes an audit event. The caller commits.
"""

import uuid
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor, Permission, PermissionDenied, load_actor
from finrecon_ai.cases.lifecycle import TRANSITIONS, allowed_closure_reasons
from finrecon_ai.db.enums import (
    ActorType,
    CaseStatus,
    ClosureReason,
    EvidenceType,
    ExceptionCategory,
    ExceptionStatus,
    Severity,
)
from finrecon_ai.db.models import (
    InvestigationCase,
    InvestigationEvidence,
    ReconciliationException,
    ReconciliationRun,
    Transaction,
    exception_transactions,
)

CASE_NUMBER_PREFIX = "CASE-"
CASE_NUMBER_LOCK = 2  # advisory-lock namespace for case numbering (audit uses the single-key space)
SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}
CATEGORY_LABELS = {
    ExceptionCategory.UNMATCHED: "Unmatched record",
    ExceptionCategory.PARTIALLY_MATCHED: "Partial match",
    ExceptionCategory.CONFIRMED_DUPLICATE: "Confirmed duplicate",
    ExceptionCategory.POTENTIAL_DUPLICATE: "Potential duplicate",
    ExceptionCategory.AMOUNT_MISMATCH: "Amount mismatch",
    ExceptionCategory.DATE_MISMATCH: "Late settlement",
    ExceptionCategory.CURRENCY_MISMATCH: "Currency mismatch",
    ExceptionCategory.FEE_DISCREPANCY: "Fee discrepancy",
    ExceptionCategory.MISSING_SETTLEMENT: "Missing settlement",
    ExceptionCategory.ANOMALY: "Anomaly",
}


class CaseError(Exception):
    pass


class CaseNotFound(CaseError):
    pass


class InvalidTransition(CaseError):
    pass


class CasePolicy(BaseModel):
    """Which exceptions become cases (FR-05: 'each qualifying exception')."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_severity: Severity = Severity.LOW
    excluded_categories: tuple[ExceptionCategory, ...] = ()

    def qualifies(self, exc: ReconciliationException) -> bool:
        return (exc.category not in self.excluded_categories
                and SEVERITY_RANK[exc.severity] >= SEVERITY_RANK[self.min_severity])


DEFAULT_CASE_POLICY = CasePolicy()


@dataclass(frozen=True)
class CaseCreationResult:
    created: list[InvestigationCase]
    not_qualifying: int


def create_cases_for_run(session: Session, actor: Actor, run_id: uuid.UUID,
                         policy: CasePolicy = DEFAULT_CASE_POLICY) -> CaseCreationResult:
    """Idempotent: exceptions that already have a case are skipped."""
    actor.require(Permission.CASE_CREATE)
    run = session.scalar(select(ReconciliationRun).where(ReconciliationRun.id == run_id,
                                                         ReconciliationRun.tenant_id == actor.tenant_id))
    if run is None:
        raise CaseNotFound("reconciliation run not found")

    open_exceptions = session.scalars(
        select(ReconciliationException)
        .where(ReconciliationException.run_id == run_id, ReconciliationException.status == ExceptionStatus.OPEN)
        .order_by(ReconciliationException.created_at, ReconciliationException.id)
    ).all()
    qualifying = [e for e in open_exceptions if policy.qualifies(e)]
    if not qualifying:
        return CaseCreationResult(created=[], not_qualifying=len(open_exceptions))

    references = _source_references(session, [e.id for e in qualifying])
    numbers = _allocate_case_numbers(session, actor.tenant_id, len(qualifying))
    rows = [{
        "id": uuid.uuid4(),
        "tenant_id": actor.tenant_id,
        "case_number": number,
        "exception_id": exc.id,
        "status": CaseStatus.NEW,
        "priority": exc.severity,
        "title": _title(exc, references.get(exc.id, [])),
        "discrepancy_amount": exc.discrepancy_amount,
        "currency": exc.currency,
        "created_by": actor.user_id,
    } for exc, number in zip(qualifying, numbers, strict=True)]
    session.execute(insert(InvestigationCase), rows)
    session.execute(update(ReconciliationException)
                    .where(ReconciliationException.id.in_([e.id for e in qualifying]))
                    .values(status=ExceptionStatus.CASE_CREATED))

    cases = session.scalars(select(InvestigationCase).where(InvestigationCase.id.in_([r["id"] for r in rows]))
                            .order_by(InvestigationCase.case_number)).all()
    exceptions_by_id = {e.id: e for e in qualifying}
    for case in cases:
        exc = exceptions_by_id[case.exception_id]
        _audit(session, actor, case, "case.created", {
            "case_number": case.case_number, "exception_id": exc.id, "run_id": run_id,
            "category": exc.category, "priority": case.priority,
        })
    return CaseCreationResult(created=list(cases), not_qualifying=len(open_exceptions) - len(qualifying))


def get_case(session: Session, actor: Actor, case_id: uuid.UUID, *, for_update: bool = False) -> InvestigationCase:
    actor.require(Permission.CASE_READ)
    query = select(InvestigationCase).where(InvestigationCase.id == case_id,
                                            InvestigationCase.tenant_id == actor.tenant_id)
    case = session.scalar(query.with_for_update() if for_update else query)
    if case is None:  # same answer for "missing" and "other tenant's": no existence leak
        raise CaseNotFound("case not found")
    return case


def list_cases(session: Session, actor: Actor, *, status: CaseStatus | None = None,
               assigned_to: uuid.UUID | None = None) -> list[InvestigationCase]:
    actor.require(Permission.CASE_READ)
    query = select(InvestigationCase).where(InvestigationCase.tenant_id == actor.tenant_id)
    if status is not None:
        query = query.where(InvestigationCase.status == status)
    if assigned_to is not None:
        query = query.where(InvestigationCase.assigned_to == assigned_to)
    return list(session.scalars(query.order_by(InvestigationCase.case_number)))


def assign_case(session: Session, actor: Actor, case_id: uuid.UUID, assignee_id: uuid.UUID) -> InvestigationCase:
    actor.require(Permission.CASE_ASSIGN)
    case = get_case(session, actor, case_id, for_update=True)
    if case.status is CaseStatus.CLOSED:
        raise InvalidTransition("closed cases cannot be reassigned")
    try:
        assignee = load_actor(session, actor.tenant_id, assignee_id)
    except PermissionDenied:
        raise CaseError("assignee is not an active user in this tenant") from None
    if not assignee.can(Permission.CASE_WORK):
        raise CaseError("assignee's role cannot work on cases")

    previous = case.assigned_to
    case.assigned_to = assignee_id
    session.flush()
    _audit(session, actor, case, "case.assigned", {"from": previous, "to": assignee_id})
    return case


def transition_case(session: Session, actor: Actor, case_id: uuid.UUID, to_status: CaseStatus, *,
                    comment: str | None = None, closure_reason: ClosureReason | None = None) -> InvestigationCase:
    case = get_case(session, actor, case_id, for_update=True)
    current = case.status
    rule = TRANSITIONS[current].get(to_status)
    if rule is None:
        raise InvalidTransition(f"cannot move a case from {current} to {to_status}")
    if rule.approval_only:
        raise InvalidTransition(f"{current} -> {to_status} is decided through the approval workflow")
    actor.require(rule.permission)
    _require_assignment(actor, case)

    comment = (comment or "").strip() or None
    if rule.requires_comment and comment is None:
        raise InvalidTransition(f"moving to {to_status} requires a comment")
    if to_status is CaseStatus.CLOSED:
        allowed = allowed_closure_reasons(current)
        if closure_reason is None and len(allowed) == 1:
            closure_reason = next(iter(allowed))
        if closure_reason not in allowed:
            raise InvalidTransition(f"closing from {current} needs one of: {sorted(allowed)}")
    elif closure_reason is not None:
        raise InvalidTransition("closure_reason only applies when closing")

    now = datetime.now(UTC)
    case.status = to_status
    if to_status is CaseStatus.RESOLVED:
        case.resolved_at = now
    if to_status is CaseStatus.CLOSED:
        case.closed_at = now
        case.closure_reason = closure_reason
    session.flush()
    _audit(session, actor, case, "case.status_changed",
           {"from": current, "to": to_status, "comment": comment, "closure_reason": closure_reason})
    return case


def add_note(session: Session, actor: Actor, case_id: uuid.UUID, text: str) -> InvestigationEvidence:
    actor.require(Permission.CASE_WORK)
    case = get_case(session, actor, case_id)
    _require_assignment(actor, case)
    if case.status is CaseStatus.CLOSED:
        raise InvalidTransition("closed cases cannot be changed")
    text = text.strip()
    if not text:
        raise CaseError("note is empty")

    note = InvestigationEvidence(tenant_id=actor.tenant_id, case_id=case.id, evidence_type=EvidenceType.ANALYST_NOTE,
                                 excerpt=text, collected_by_user_id=actor.user_id)
    session.add(note)
    session.flush()
    _audit(session, actor, case, "case.note_added", {"evidence_id": note.id, "text": text})
    return note


# --- helpers ---


def _require_assignment(actor: Actor, case: InvestigationCase) -> None:
    if not actor.can(Permission.CASE_WORK_ANY) and case.assigned_to != actor.user_id:
        raise PermissionDenied("case is not assigned to you")


def _allocate_case_numbers(session: Session, tenant_id: uuid.UUID, count: int) -> list[str]:
    # Serialize numbering per tenant; the lock is released at commit/rollback.
    tenant_key = zlib.crc32(tenant_id.bytes) - 2**31
    session.execute(select(func.pg_advisory_xact_lock(CASE_NUMBER_LOCK, tenant_key)))
    last = session.scalar(select(func.max(InvestigationCase.case_number))
                          .where(InvestigationCase.tenant_id == tenant_id))
    start = int(last.removeprefix(CASE_NUMBER_PREFIX)) + 1 if last else 1
    return [f"{CASE_NUMBER_PREFIX}{n:06d}" for n in range(start, start + count)]


def _source_references(session: Session, exception_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    rows = session.execute(
        select(exception_transactions.c.exception_id, Transaction.source_transaction_id)
        .join(Transaction, Transaction.id == exception_transactions.c.transaction_id)
        .where(exception_transactions.c.exception_id.in_(exception_ids))
        .order_by(Transaction.source_transaction_id)
    ).all()
    refs: dict[uuid.UUID, list[str]] = {}
    for exc_id, ref in rows:
        refs.setdefault(exc_id, []).append(ref or "?")
    return refs


def _title(exc: ReconciliationException, refs: list[str]) -> str:
    shown = ", ".join(refs[:3]) + (f" +{len(refs) - 3}" if len(refs) > 3 else "")
    title = f"{CATEGORY_LABELS[exc.category]}: {shown}" if shown else CATEGORY_LABELS[exc.category]
    if exc.discrepancy_amount is not None and exc.currency:
        title += f" ({_money(exc.discrepancy_amount)} {exc.currency})"
    return title[:300]


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,}"


def _audit(session: Session, actor: Actor, case: InvestigationCase, event_type: str, payload: dict) -> None:
    record_event(session, tenant_id=actor.tenant_id, event_type=event_type, entity_type="investigation_case",
                 entity_id=case.id, actor_type=ActorType.SYSTEM if actor.is_system else ActorType.USER,
                 actor_user_id=actor.user_id, payload=payload)

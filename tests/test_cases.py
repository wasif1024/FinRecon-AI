"""Case management against the test database: creation, assignment, lifecycle, permissions, audit."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finrecon_ai.audit import verify_chain
from finrecon_ai.authz import Actor, PermissionDenied, load_actor
from finrecon_ai.cases.service import (
    CaseError,
    CaseNotFound,
    CasePolicy,
    InvalidTransition,
    add_note,
    assign_case,
    create_cases_for_run,
    get_case,
    list_cases,
    transition_case,
)
from finrecon_ai.db.enums import CaseStatus, ClosureReason, ExceptionStatus, RoleName, Severity
from finrecon_ai.db.models import (
    AuditEvent,
    InvestigationCase,
    ReconciliationException,
    Tenant,
    User,
)


def exception_count(session, run_id, status=None):
    query = select(func.count()).select_from(ReconciliationException).where(ReconciliationException.run_id == run_id)
    if status is not None:
        query = query.where(ReconciliationException.status == status)
    return session.scalar(query)


# --- creation ---


def test_one_case_per_exception_with_sequential_numbers(session, world, case_ids):
    tenant, run, _ = world
    total = exception_count(session, run.id)
    assert len(case_ids) == total
    assert exception_count(session, run.id, ExceptionStatus.CASE_CREATED) == total

    cases = list_cases(session, Actor.system(tenant.id))
    assert [c.case_number for c in cases] == [f"CASE-{n:06d}" for n in range(1, total + 1)]
    assert all(c.status is CaseStatus.NEW and c.title for c in cases)


def test_case_creation_is_idempotent(session, world, case_ids):
    tenant, run, _ = world
    again = create_cases_for_run(session, Actor.system(tenant.id), run.id)
    assert again.created == []


def test_policy_limits_cases_and_numbering_continues(session, world):
    tenant, run, _ = world
    system = Actor.system(tenant.id)
    strict = create_cases_for_run(session, system, run.id, CasePolicy(min_severity=Severity.HIGH))
    assert strict.created and all(c.priority in (Severity.HIGH, Severity.CRITICAL) for c in strict.created)
    assert strict.not_qualifying == exception_count(session, run.id, ExceptionStatus.OPEN)

    rest = create_cases_for_run(session, system, run.id)
    first_rest = min(int(c.case_number[5:]) for c in rest.created)
    assert first_rest == len(strict.created) + 1


def test_only_system_creates_cases(session, world):
    tenant, run, users = world
    for role in RoleName:
        with pytest.raises(PermissionDenied):
            create_cases_for_run(session, users[role], run.id)


def test_each_created_case_is_audited(session, world, case_ids):
    tenant, _, _ = world
    created_events = session.scalar(select(func.count()).select_from(AuditEvent)
                                    .where(AuditEvent.tenant_id == tenant.id, AuditEvent.event_type == "case.created"))
    assert created_events == len(case_ids)


# --- assignment ---


def test_manager_assigns_analyst(session, world, case_ids):
    _, _, users = world
    case = assign_case(session, users[RoleName.MANAGER], case_ids[0], users[RoleName.ANALYST].user_id)
    assert case.assigned_to == users[RoleName.ANALYST].user_id


@pytest.mark.parametrize("role", [RoleName.ANALYST, RoleName.ADMIN, RoleName.AUDITOR])
def test_only_managers_assign(session, world, case_ids, role):
    _, _, users = world
    with pytest.raises(PermissionDenied):
        assign_case(session, users[role], case_ids[0], users[RoleName.ANALYST].user_id)


def test_cannot_assign_to_role_that_cannot_work(session, world, case_ids):
    _, _, users = world
    with pytest.raises(CaseError, match="cannot work"):
        assign_case(session, users[RoleName.MANAGER], case_ids[0], users[RoleName.AUDITOR].user_id)


def test_cannot_assign_to_user_of_another_tenant(session, world, case_ids):
    _, _, users = world
    other = Tenant(name="Other", slug="other")
    session.add(other)
    session.flush()
    outsider = User(tenant_id=other.id, email="x@other.dev", full_name="Outsider")
    session.add(outsider)
    session.flush()
    with pytest.raises(CaseError, match="not an active user"):
        assign_case(session, users[RoleName.MANAGER], case_ids[0], outsider.id)


# --- lifecycle ---


def test_analyst_works_assigned_case_up_to_approval(session, world, case_ids):
    _, _, users = world
    analyst, manager = users[RoleName.ANALYST], users[RoleName.MANAGER]
    case_id = case_ids[0]
    assign_case(session, manager, case_id, analyst.user_id)

    transition_case(session, analyst, case_id, CaseStatus.INVESTIGATING)
    transition_case(session, analyst, case_id, CaseStatus.AWAITING_EVIDENCE, comment="need fee schedule")
    transition_case(session, analyst, case_id, CaseStatus.INVESTIGATING)
    case = transition_case(session, analyst, case_id, CaseStatus.AWAITING_APPROVAL)
    assert case.status is CaseStatus.AWAITING_APPROVAL

    # Decisions belong to the approval workflow (Phase 4), even for a manager.
    for target in (CaseStatus.APPROVED, CaseStatus.REJECTED, CaseStatus.INVESTIGATING):
        with pytest.raises(InvalidTransition, match="approval workflow"):
            transition_case(session, manager, case_id, target, comment="ok")


def test_analyst_cannot_work_unassigned_case_but_manager_can(session, world, case_ids):
    _, _, users = world
    with pytest.raises(PermissionDenied, match="not assigned"):
        transition_case(session, users[RoleName.ANALYST], case_ids[0], CaseStatus.INVESTIGATING)
    case = transition_case(session, users[RoleName.MANAGER], case_ids[0], CaseStatus.INVESTIGATING)
    assert case.status is CaseStatus.INVESTIGATING


@pytest.mark.parametrize("role", [RoleName.AUDITOR, RoleName.ADMIN])
def test_read_only_roles_cannot_change_cases(session, world, case_ids, role):
    _, _, users = world
    with pytest.raises(PermissionDenied):
        transition_case(session, users[role], case_ids[0], CaseStatus.INVESTIGATING)
    with pytest.raises(PermissionDenied):
        add_note(session, users[role], case_ids[0], "hello")


def test_invalid_transition_rejected(session, world, case_ids):
    _, _, users = world
    with pytest.raises(InvalidTransition, match="cannot move"):
        transition_case(session, users[RoleName.MANAGER], case_ids[0], CaseStatus.RESOLVED)


def test_dismissal_needs_comment_and_dismissal_reason(session, world, case_ids):
    _, _, users = world
    manager = users[RoleName.MANAGER]
    with pytest.raises(InvalidTransition, match="requires a comment"):
        transition_case(session, manager, case_ids[0], CaseStatus.CLOSED, closure_reason=ClosureReason.FALSE_POSITIVE)
    with pytest.raises(InvalidTransition, match="needs one of"):
        transition_case(session, manager, case_ids[0], CaseStatus.CLOSED, comment="x",
                        closure_reason=ClosureReason.RESOLVED)

    case = transition_case(session, manager, case_ids[0], CaseStatus.CLOSED, comment="expected bank charge",
                           closure_reason=ClosureReason.NOT_ACTIONABLE)
    assert case.closure_reason is ClosureReason.NOT_ACTIONABLE and case.closed_at is not None

    with pytest.raises(InvalidTransition):
        add_note(session, manager, case_ids[0], "too late")
    with pytest.raises(InvalidTransition):
        assign_case(session, manager, case_ids[0], users[RoleName.ANALYST].user_id)


def test_database_requires_reason_when_closed(session, world, case_ids):
    case = session.get(InvestigationCase, case_ids[0])
    case.status = CaseStatus.CLOSED  # bypassing the service
    with pytest.raises(IntegrityError, match="closed_has_reason"):
        with session.begin_nested():
            session.flush()


def test_note_is_stored_as_evidence(session, world, case_ids):
    _, _, users = world
    note = add_note(session, users[RoleName.MANAGER], case_ids[0], "  Called PayGate support.  ")
    assert note.excerpt == "Called PayGate support."
    assert note.collected_by_user_id == users[RoleName.MANAGER].user_id


# --- isolation and audit ---


def test_other_tenant_cannot_see_case(session, world, case_ids):
    stranger = Actor(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), roles=frozenset({RoleName.MANAGER}))
    with pytest.raises(CaseNotFound):
        get_case(session, stranger, case_ids[0])
    with pytest.raises(CaseNotFound):
        transition_case(session, stranger, case_ids[0], CaseStatus.INVESTIGATING)


def test_every_change_audited_and_chain_intact(session, world, case_ids):
    tenant, _, users = world
    manager, analyst = users[RoleName.MANAGER], users[RoleName.ANALYST]
    assign_case(session, manager, case_ids[1], analyst.user_id)
    transition_case(session, analyst, case_ids[1], CaseStatus.INVESTIGATING)
    add_note(session, analyst, case_ids[1], "checked settlement report")

    events = session.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == str(case_ids[1]))
                             .order_by(AuditEvent.id)).all()
    assert events == ["case.created", "case.assigned", "case.status_changed", "case.note_added"]
    session.expire_all()
    assert verify_chain(session, tenant.id) is None

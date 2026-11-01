"""Investigation case state machine (FR-05). Any move not listed here is invalid."""

from dataclasses import dataclass

from finrecon_ai.authz import Permission
from finrecon_ai.db.enums import CaseStatus, ClosureReason


@dataclass(frozen=True)
class Transition:
    permission: Permission
    requires_comment: bool = False
    # Only the approval workflow (Phase 4) may take this step; it records the decision in approval_decisions.
    approval_only: bool = False


_WORK = Transition(Permission.CASE_WORK)
_WORK_EXPLAINED = Transition(Permission.CASE_WORK, requires_comment=True)
_DECISION = Transition(Permission.CASE_DECIDE, requires_comment=True, approval_only=True)

TRANSITIONS: dict[CaseStatus, dict[CaseStatus, Transition]] = {
    CaseStatus.NEW: {
        CaseStatus.INVESTIGATING: _WORK,
        CaseStatus.CLOSED: _WORK_EXPLAINED,  # dismissed before investigation
    },
    CaseStatus.INVESTIGATING: {
        CaseStatus.AWAITING_EVIDENCE: _WORK_EXPLAINED,  # comment says what is missing
        CaseStatus.AWAITING_APPROVAL: _WORK,
        CaseStatus.CLOSED: _WORK_EXPLAINED,
    },
    CaseStatus.AWAITING_EVIDENCE: {
        CaseStatus.INVESTIGATING: _WORK,
    },
    CaseStatus.AWAITING_APPROVAL: {
        CaseStatus.APPROVED: _DECISION,
        CaseStatus.REJECTED: _DECISION,
        CaseStatus.AWAITING_EVIDENCE: _DECISION,  # "request additional evidence"
        CaseStatus.INVESTIGATING: _DECISION,  # "return for investigation"
    },
    CaseStatus.APPROVED: {
        CaseStatus.RESOLVED: _WORK,
    },
    CaseStatus.REJECTED: {
        CaseStatus.INVESTIGATING: _WORK,
        CaseStatus.CLOSED: _WORK_EXPLAINED,
    },
    CaseStatus.RESOLVED: {
        CaseStatus.CLOSED: _WORK,
    },
    CaseStatus.CLOSED: {},
}

# Closing after resolution means "resolved"; closing any earlier means the case was dismissed.
DISMISSAL_REASONS = frozenset({ClosureReason.FALSE_POSITIVE, ClosureReason.NOT_ACTIONABLE})


def allowed_closure_reasons(current: CaseStatus) -> frozenset[ClosureReason]:
    if current is CaseStatus.RESOLVED:
        return frozenset({ClosureReason.RESOLVED})
    return DISMISSAL_REASONS

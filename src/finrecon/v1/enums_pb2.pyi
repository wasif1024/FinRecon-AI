from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from typing import ClassVar as _ClassVar

DESCRIPTOR: _descriptor.FileDescriptor

class CaseStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CASE_STATUS_UNSPECIFIED: _ClassVar[CaseStatus]
    CASE_STATUS_NEW: _ClassVar[CaseStatus]
    CASE_STATUS_INVESTIGATING: _ClassVar[CaseStatus]
    CASE_STATUS_AWAITING_EVIDENCE: _ClassVar[CaseStatus]
    CASE_STATUS_AWAITING_APPROVAL: _ClassVar[CaseStatus]
    CASE_STATUS_APPROVED: _ClassVar[CaseStatus]
    CASE_STATUS_REJECTED: _ClassVar[CaseStatus]
    CASE_STATUS_RESOLVED: _ClassVar[CaseStatus]
    CASE_STATUS_CLOSED: _ClassVar[CaseStatus]

class Severity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEVERITY_UNSPECIFIED: _ClassVar[Severity]
    SEVERITY_LOW: _ClassVar[Severity]
    SEVERITY_MEDIUM: _ClassVar[Severity]
    SEVERITY_HIGH: _ClassVar[Severity]
    SEVERITY_CRITICAL: _ClassVar[Severity]

class ClosureReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CLOSURE_REASON_UNSPECIFIED: _ClassVar[ClosureReason]
    CLOSURE_REASON_RESOLVED: _ClassVar[ClosureReason]
    CLOSURE_REASON_FALSE_POSITIVE: _ClassVar[ClosureReason]
    CLOSURE_REASON_NOT_ACTIONABLE: _ClassVar[ClosureReason]

class Direction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DIRECTION_UNSPECIFIED: _ClassVar[Direction]
    DIRECTION_DEBIT: _ClassVar[Direction]
    DIRECTION_CREDIT: _ClassVar[Direction]

class SourceType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SOURCE_TYPE_UNSPECIFIED: _ClassVar[SourceType]
    SOURCE_TYPE_BANK_STATEMENT: _ClassVar[SourceType]
    SOURCE_TYPE_PAYMENT_GATEWAY: _ClassVar[SourceType]
    SOURCE_TYPE_MERCHANT_SETTLEMENT: _ClassVar[SourceType]

class ExceptionCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXCEPTION_CATEGORY_UNSPECIFIED: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_UNMATCHED: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_PARTIALLY_MATCHED: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_CONFIRMED_DUPLICATE: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_POTENTIAL_DUPLICATE: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_AMOUNT_MISMATCH: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_DATE_MISMATCH: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_CURRENCY_MISMATCH: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_FEE_DISCREPANCY: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_MISSING_SETTLEMENT: _ClassVar[ExceptionCategory]
    EXCEPTION_CATEGORY_ANOMALY: _ClassVar[ExceptionCategory]

class EvidenceType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVIDENCE_TYPE_UNSPECIFIED: _ClassVar[EvidenceType]
    EVIDENCE_TYPE_TRANSACTION: _ClassVar[EvidenceType]
    EVIDENCE_TYPE_POLICY_PASSAGE: _ClassVar[EvidenceType]
    EVIDENCE_TYPE_SUPPORTING_DOCUMENT: _ClassVar[EvidenceType]
    EVIDENCE_TYPE_HISTORICAL_CASE: _ClassVar[EvidenceType]
    EVIDENCE_TYPE_ANALYST_NOTE: _ClassVar[EvidenceType]

class FindingType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FINDING_TYPE_UNSPECIFIED: _ClassVar[FindingType]
    FINDING_TYPE_VERIFIED_FACT: _ClassVar[FindingType]
    FINDING_TYPE_HYPOTHESIS: _ClassVar[FindingType]
    FINDING_TYPE_OUTSTANDING_REQUIREMENT: _ClassVar[FindingType]

class ResolutionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RESOLUTION_TYPE_UNSPECIFIED: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_CONFIRM_FEE_DISCREPANCY: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_REQUEST_DOCUMENTATION: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_INVESTIGATE_DUPLICATE: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_ESCALATE: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_MANUAL_MATCH: _ClassVar[ResolutionType]
    RESOLUTION_TYPE_NO_ACTION_REQUIRED: _ClassVar[ResolutionType]

class RecommendationStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RECOMMENDATION_STATUS_UNSPECIFIED: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_DRAFT: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_PENDING_APPROVAL: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_APPROVED: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_REJECTED: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_SUPERSEDED: _ClassVar[RecommendationStatus]
    RECOMMENDATION_STATUS_EXECUTED: _ClassVar[RecommendationStatus]

class ApprovalRequestStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    APPROVAL_REQUEST_STATUS_UNSPECIFIED: _ClassVar[ApprovalRequestStatus]
    APPROVAL_REQUEST_STATUS_PENDING: _ClassVar[ApprovalRequestStatus]
    APPROVAL_REQUEST_STATUS_DECIDED: _ClassVar[ApprovalRequestStatus]
    APPROVAL_REQUEST_STATUS_CANCELLED: _ClassVar[ApprovalRequestStatus]

class ApprovalDecisionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    APPROVAL_DECISION_TYPE_UNSPECIFIED: _ClassVar[ApprovalDecisionType]
    APPROVAL_DECISION_TYPE_APPROVE: _ClassVar[ApprovalDecisionType]
    APPROVAL_DECISION_TYPE_REJECT: _ClassVar[ApprovalDecisionType]
    APPROVAL_DECISION_TYPE_REQUEST_EVIDENCE: _ClassVar[ApprovalDecisionType]
    APPROVAL_DECISION_TYPE_RETURN_FOR_INVESTIGATION: _ClassVar[ApprovalDecisionType]
CASE_STATUS_UNSPECIFIED: CaseStatus
CASE_STATUS_NEW: CaseStatus
CASE_STATUS_INVESTIGATING: CaseStatus
CASE_STATUS_AWAITING_EVIDENCE: CaseStatus
CASE_STATUS_AWAITING_APPROVAL: CaseStatus
CASE_STATUS_APPROVED: CaseStatus
CASE_STATUS_REJECTED: CaseStatus
CASE_STATUS_RESOLVED: CaseStatus
CASE_STATUS_CLOSED: CaseStatus
SEVERITY_UNSPECIFIED: Severity
SEVERITY_LOW: Severity
SEVERITY_MEDIUM: Severity
SEVERITY_HIGH: Severity
SEVERITY_CRITICAL: Severity
CLOSURE_REASON_UNSPECIFIED: ClosureReason
CLOSURE_REASON_RESOLVED: ClosureReason
CLOSURE_REASON_FALSE_POSITIVE: ClosureReason
CLOSURE_REASON_NOT_ACTIONABLE: ClosureReason
DIRECTION_UNSPECIFIED: Direction
DIRECTION_DEBIT: Direction
DIRECTION_CREDIT: Direction
SOURCE_TYPE_UNSPECIFIED: SourceType
SOURCE_TYPE_BANK_STATEMENT: SourceType
SOURCE_TYPE_PAYMENT_GATEWAY: SourceType
SOURCE_TYPE_MERCHANT_SETTLEMENT: SourceType
EXCEPTION_CATEGORY_UNSPECIFIED: ExceptionCategory
EXCEPTION_CATEGORY_UNMATCHED: ExceptionCategory
EXCEPTION_CATEGORY_PARTIALLY_MATCHED: ExceptionCategory
EXCEPTION_CATEGORY_CONFIRMED_DUPLICATE: ExceptionCategory
EXCEPTION_CATEGORY_POTENTIAL_DUPLICATE: ExceptionCategory
EXCEPTION_CATEGORY_AMOUNT_MISMATCH: ExceptionCategory
EXCEPTION_CATEGORY_DATE_MISMATCH: ExceptionCategory
EXCEPTION_CATEGORY_CURRENCY_MISMATCH: ExceptionCategory
EXCEPTION_CATEGORY_FEE_DISCREPANCY: ExceptionCategory
EXCEPTION_CATEGORY_MISSING_SETTLEMENT: ExceptionCategory
EXCEPTION_CATEGORY_ANOMALY: ExceptionCategory
EVIDENCE_TYPE_UNSPECIFIED: EvidenceType
EVIDENCE_TYPE_TRANSACTION: EvidenceType
EVIDENCE_TYPE_POLICY_PASSAGE: EvidenceType
EVIDENCE_TYPE_SUPPORTING_DOCUMENT: EvidenceType
EVIDENCE_TYPE_HISTORICAL_CASE: EvidenceType
EVIDENCE_TYPE_ANALYST_NOTE: EvidenceType
FINDING_TYPE_UNSPECIFIED: FindingType
FINDING_TYPE_VERIFIED_FACT: FindingType
FINDING_TYPE_HYPOTHESIS: FindingType
FINDING_TYPE_OUTSTANDING_REQUIREMENT: FindingType
RESOLUTION_TYPE_UNSPECIFIED: ResolutionType
RESOLUTION_TYPE_CONFIRM_FEE_DISCREPANCY: ResolutionType
RESOLUTION_TYPE_REQUEST_DOCUMENTATION: ResolutionType
RESOLUTION_TYPE_INVESTIGATE_DUPLICATE: ResolutionType
RESOLUTION_TYPE_ESCALATE: ResolutionType
RESOLUTION_TYPE_MANUAL_MATCH: ResolutionType
RESOLUTION_TYPE_NO_ACTION_REQUIRED: ResolutionType
RECOMMENDATION_STATUS_UNSPECIFIED: RecommendationStatus
RECOMMENDATION_STATUS_DRAFT: RecommendationStatus
RECOMMENDATION_STATUS_PENDING_APPROVAL: RecommendationStatus
RECOMMENDATION_STATUS_APPROVED: RecommendationStatus
RECOMMENDATION_STATUS_REJECTED: RecommendationStatus
RECOMMENDATION_STATUS_SUPERSEDED: RecommendationStatus
RECOMMENDATION_STATUS_EXECUTED: RecommendationStatus
APPROVAL_REQUEST_STATUS_UNSPECIFIED: ApprovalRequestStatus
APPROVAL_REQUEST_STATUS_PENDING: ApprovalRequestStatus
APPROVAL_REQUEST_STATUS_DECIDED: ApprovalRequestStatus
APPROVAL_REQUEST_STATUS_CANCELLED: ApprovalRequestStatus
APPROVAL_DECISION_TYPE_UNSPECIFIED: ApprovalDecisionType
APPROVAL_DECISION_TYPE_APPROVE: ApprovalDecisionType
APPROVAL_DECISION_TYPE_REJECT: ApprovalDecisionType
APPROVAL_DECISION_TYPE_REQUEST_EVIDENCE: ApprovalDecisionType
APPROVAL_DECISION_TYPE_RETURN_FOR_INVESTIGATION: ApprovalDecisionType

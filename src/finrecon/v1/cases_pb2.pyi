import datetime

from finrecon.v1 import enums_pb2 as _enums_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CaseSummary(_message.Message):
    __slots__ = ("id", "case_number", "title", "status", "priority", "discrepancy_amount", "currency", "assigned_to", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CASE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    DISCREPANCY_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_TO_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    case_number: str
    title: str
    status: _enums_pb2.CaseStatus
    priority: _enums_pb2.Severity
    discrepancy_amount: str
    currency: str
    assigned_to: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., case_number: _Optional[str] = ..., title: _Optional[str] = ..., status: _Optional[_Union[_enums_pb2.CaseStatus, str]] = ..., priority: _Optional[_Union[_enums_pb2.Severity, str]] = ..., discrepancy_amount: _Optional[str] = ..., currency: _Optional[str] = ..., assigned_to: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ReconciliationException(_message.Message):
    __slots__ = ("id", "category", "severity", "rule_code", "details")
    ID_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    RULE_CODE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    id: str
    category: _enums_pb2.ExceptionCategory
    severity: _enums_pb2.Severity
    rule_code: str
    details: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., category: _Optional[_Union[_enums_pb2.ExceptionCategory, str]] = ..., severity: _Optional[_Union[_enums_pb2.Severity, str]] = ..., rule_code: _Optional[str] = ..., details: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Transaction(_message.Message):
    __slots__ = ("id", "source_type", "source_transaction_id", "external_reference", "transaction_date", "settlement_date", "amount", "fee_amount", "currency", "direction", "counterparty", "merchant", "description", "raw_payload")
    ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    TRANSACTION_DATE_FIELD_NUMBER: _ClassVar[int]
    SETTLEMENT_DATE_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    FEE_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    COUNTERPARTY_FIELD_NUMBER: _ClassVar[int]
    MERCHANT_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    RAW_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    id: str
    source_type: _enums_pb2.SourceType
    source_transaction_id: str
    external_reference: str
    transaction_date: str
    settlement_date: str
    amount: str
    fee_amount: str
    currency: str
    direction: _enums_pb2.Direction
    counterparty: str
    merchant: str
    description: str
    raw_payload: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., source_type: _Optional[_Union[_enums_pb2.SourceType, str]] = ..., source_transaction_id: _Optional[str] = ..., external_reference: _Optional[str] = ..., transaction_date: _Optional[str] = ..., settlement_date: _Optional[str] = ..., amount: _Optional[str] = ..., fee_amount: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[_Union[_enums_pb2.Direction, str]] = ..., counterparty: _Optional[str] = ..., merchant: _Optional[str] = ..., description: _Optional[str] = ..., raw_payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Match(_message.Message):
    __slots__ = ("id", "classification", "strategy", "score", "explanation")
    ID_FIELD_NUMBER: _ClassVar[int]
    CLASSIFICATION_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    EXPLANATION_FIELD_NUMBER: _ClassVar[int]
    id: str
    classification: str
    strategy: str
    score: str
    explanation: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., classification: _Optional[str] = ..., strategy: _Optional[str] = ..., score: _Optional[str] = ..., explanation: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Evidence(_message.Message):
    __slots__ = ("id", "evidence_type", "transaction_id", "policy_document_id", "related_case_id", "source_reference", "excerpt", "agent_execution_id", "collected_by_user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_TYPE_FIELD_NUMBER: _ClassVar[int]
    TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    POLICY_DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATED_CASE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    EXCERPT_FIELD_NUMBER: _ClassVar[int]
    AGENT_EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTED_BY_USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    evidence_type: _enums_pb2.EvidenceType
    transaction_id: str
    policy_document_id: str
    related_case_id: str
    source_reference: _struct_pb2.Struct
    excerpt: str
    agent_execution_id: str
    collected_by_user_id: str
    def __init__(self, id: _Optional[str] = ..., evidence_type: _Optional[_Union[_enums_pb2.EvidenceType, str]] = ..., transaction_id: _Optional[str] = ..., policy_document_id: _Optional[str] = ..., related_case_id: _Optional[str] = ..., source_reference: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., excerpt: _Optional[str] = ..., agent_execution_id: _Optional[str] = ..., collected_by_user_id: _Optional[str] = ...) -> None: ...

class Finding(_message.Message):
    __slots__ = ("id", "finding_type", "statement", "confidence", "evidence_ids", "agent_execution_id", "recorded_by")
    ID_FIELD_NUMBER: _ClassVar[int]
    FINDING_TYPE_FIELD_NUMBER: _ClassVar[int]
    STATEMENT_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_IDS_FIELD_NUMBER: _ClassVar[int]
    AGENT_EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    RECORDED_BY_FIELD_NUMBER: _ClassVar[int]
    id: str
    finding_type: _enums_pb2.FindingType
    statement: str
    confidence: str
    evidence_ids: _containers.RepeatedScalarFieldContainer[str]
    agent_execution_id: str
    recorded_by: str
    def __init__(self, id: _Optional[str] = ..., finding_type: _Optional[_Union[_enums_pb2.FindingType, str]] = ..., statement: _Optional[str] = ..., confidence: _Optional[str] = ..., evidence_ids: _Optional[_Iterable[str]] = ..., agent_execution_id: _Optional[str] = ..., recorded_by: _Optional[str] = ...) -> None: ...

class Recommendation(_message.Message):
    __slots__ = ("id", "version", "resolution_type", "status", "summary", "rationale", "proposed_action", "confidence", "execution_result", "executed_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    RESOLUTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    RATIONALE_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_ACTION_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_RESULT_FIELD_NUMBER: _ClassVar[int]
    EXECUTED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    version: int
    resolution_type: _enums_pb2.ResolutionType
    status: _enums_pb2.RecommendationStatus
    summary: str
    rationale: str
    proposed_action: _struct_pb2.Struct
    confidence: str
    execution_result: _struct_pb2.Struct
    executed_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., version: _Optional[int] = ..., resolution_type: _Optional[_Union[_enums_pb2.ResolutionType, str]] = ..., status: _Optional[_Union[_enums_pb2.RecommendationStatus, str]] = ..., summary: _Optional[str] = ..., rationale: _Optional[str] = ..., proposed_action: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., confidence: _Optional[str] = ..., execution_result: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., executed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Decision(_message.Message):
    __slots__ = ("id", "approval_request_id", "reviewer_id", "decision", "comments", "decided_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEWER_ID_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    approval_request_id: str
    reviewer_id: str
    decision: _enums_pb2.ApprovalDecisionType
    comments: str
    decided_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., approval_request_id: _Optional[str] = ..., reviewer_id: _Optional[str] = ..., decision: _Optional[_Union[_enums_pb2.ApprovalDecisionType, str]] = ..., comments: _Optional[str] = ..., decided_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ApprovalRequest(_message.Message):
    __slots__ = ("id", "case_id", "recommendation_id", "status", "requested_by_user_id", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDATION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_BY_USER_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    case_id: str
    recommendation_id: str
    status: _enums_pb2.ApprovalRequestStatus
    requested_by_user_id: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., case_id: _Optional[str] = ..., recommendation_id: _Optional[str] = ..., status: _Optional[_Union[_enums_pb2.ApprovalRequestStatus, str]] = ..., requested_by_user_id: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TransitionOption(_message.Message):
    __slots__ = ("to_status", "requires_comment", "closure_reasons")
    TO_STATUS_FIELD_NUMBER: _ClassVar[int]
    REQUIRES_COMMENT_FIELD_NUMBER: _ClassVar[int]
    CLOSURE_REASONS_FIELD_NUMBER: _ClassVar[int]
    to_status: _enums_pb2.CaseStatus
    requires_comment: bool
    closure_reasons: _containers.RepeatedScalarFieldContainer[_enums_pb2.ClosureReason]
    def __init__(self, to_status: _Optional[_Union[_enums_pb2.CaseStatus, str]] = ..., requires_comment: _Optional[bool] = ..., closure_reasons: _Optional[_Iterable[_Union[_enums_pb2.ClosureReason, str]]] = ...) -> None: ...

class CaseActions(_message.Message):
    __slots__ = ("can_assign", "can_add_note", "can_investigate", "transitions", "pending_approval_request_id", "can_decide", "decide_blocked_reason")
    CAN_ASSIGN_FIELD_NUMBER: _ClassVar[int]
    CAN_ADD_NOTE_FIELD_NUMBER: _ClassVar[int]
    CAN_INVESTIGATE_FIELD_NUMBER: _ClassVar[int]
    TRANSITIONS_FIELD_NUMBER: _ClassVar[int]
    PENDING_APPROVAL_REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CAN_DECIDE_FIELD_NUMBER: _ClassVar[int]
    DECIDE_BLOCKED_REASON_FIELD_NUMBER: _ClassVar[int]
    can_assign: bool
    can_add_note: bool
    can_investigate: bool
    transitions: _containers.RepeatedCompositeFieldContainer[TransitionOption]
    pending_approval_request_id: str
    can_decide: bool
    decide_blocked_reason: str
    def __init__(self, can_assign: _Optional[bool] = ..., can_add_note: _Optional[bool] = ..., can_investigate: _Optional[bool] = ..., transitions: _Optional[_Iterable[_Union[TransitionOption, _Mapping]]] = ..., pending_approval_request_id: _Optional[str] = ..., can_decide: _Optional[bool] = ..., decide_blocked_reason: _Optional[str] = ...) -> None: ...

class CaseDetail(_message.Message):
    __slots__ = ("id", "case_number", "title", "status", "priority", "discrepancy_amount", "currency", "assigned_to", "created_at", "closure_reason", "resolved_at", "closed_at", "exception", "transactions", "matches", "findings", "evidence", "recommendations", "approval_requests", "decisions", "actions")
    ID_FIELD_NUMBER: _ClassVar[int]
    CASE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    DISCREPANCY_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_TO_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    CLOSURE_REASON_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_AT_FIELD_NUMBER: _ClassVar[int]
    CLOSED_AT_FIELD_NUMBER: _ClassVar[int]
    EXCEPTION_FIELD_NUMBER: _ClassVar[int]
    TRANSACTIONS_FIELD_NUMBER: _ClassVar[int]
    MATCHES_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDATIONS_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    DECISIONS_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    case_number: str
    title: str
    status: _enums_pb2.CaseStatus
    priority: _enums_pb2.Severity
    discrepancy_amount: str
    currency: str
    assigned_to: str
    created_at: _timestamp_pb2.Timestamp
    closure_reason: _enums_pb2.ClosureReason
    resolved_at: _timestamp_pb2.Timestamp
    closed_at: _timestamp_pb2.Timestamp
    exception: ReconciliationException
    transactions: _containers.RepeatedCompositeFieldContainer[Transaction]
    matches: _containers.RepeatedCompositeFieldContainer[Match]
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    evidence: _containers.RepeatedCompositeFieldContainer[Evidence]
    recommendations: _containers.RepeatedCompositeFieldContainer[Recommendation]
    approval_requests: _containers.RepeatedCompositeFieldContainer[ApprovalRequest]
    decisions: _containers.RepeatedCompositeFieldContainer[Decision]
    actions: CaseActions
    def __init__(self, id: _Optional[str] = ..., case_number: _Optional[str] = ..., title: _Optional[str] = ..., status: _Optional[_Union[_enums_pb2.CaseStatus, str]] = ..., priority: _Optional[_Union[_enums_pb2.Severity, str]] = ..., discrepancy_amount: _Optional[str] = ..., currency: _Optional[str] = ..., assigned_to: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., closure_reason: _Optional[_Union[_enums_pb2.ClosureReason, str]] = ..., resolved_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., closed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., exception: _Optional[_Union[ReconciliationException, _Mapping]] = ..., transactions: _Optional[_Iterable[_Union[Transaction, _Mapping]]] = ..., matches: _Optional[_Iterable[_Union[Match, _Mapping]]] = ..., findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., evidence: _Optional[_Iterable[_Union[Evidence, _Mapping]]] = ..., recommendations: _Optional[_Iterable[_Union[Recommendation, _Mapping]]] = ..., approval_requests: _Optional[_Iterable[_Union[ApprovalRequest, _Mapping]]] = ..., decisions: _Optional[_Iterable[_Union[Decision, _Mapping]]] = ..., actions: _Optional[_Union[CaseActions, _Mapping]] = ...) -> None: ...

class Workflow(_message.Message):
    __slots__ = ("case_id", "started", "paused_for_approval", "running", "round", "approval_request_id", "recommendation_id", "error", "rejected_output", "execution_result")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    STARTED_FIELD_NUMBER: _ClassVar[int]
    PAUSED_FOR_APPROVAL_FIELD_NUMBER: _ClassVar[int]
    RUNNING_FIELD_NUMBER: _ClassVar[int]
    ROUND_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDATION_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    REJECTED_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_RESULT_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    started: bool
    paused_for_approval: bool
    running: bool
    round: int
    approval_request_id: str
    recommendation_id: str
    error: str
    rejected_output: _containers.RepeatedScalarFieldContainer[str]
    execution_result: _struct_pb2.Struct
    def __init__(self, case_id: _Optional[str] = ..., started: _Optional[bool] = ..., paused_for_approval: _Optional[bool] = ..., running: _Optional[bool] = ..., round: _Optional[int] = ..., approval_request_id: _Optional[str] = ..., recommendation_id: _Optional[str] = ..., error: _Optional[str] = ..., rejected_output: _Optional[_Iterable[str]] = ..., execution_result: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class AuditEvent(_message.Message):
    __slots__ = ("id", "occurred_at", "event_type", "actor_type", "actor_user_id", "agent_execution_id", "entity_type", "entity_id", "payload", "request_id", "hash")
    ID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    ACTOR_TYPE_FIELD_NUMBER: _ClassVar[int]
    ACTOR_USER_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    id: int
    occurred_at: _timestamp_pb2.Timestamp
    event_type: str
    actor_type: str
    actor_user_id: str
    agent_execution_id: str
    entity_type: str
    entity_id: str
    payload: _struct_pb2.Struct
    request_id: str
    hash: str
    def __init__(self, id: _Optional[int] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., event_type: _Optional[str] = ..., actor_type: _Optional[str] = ..., actor_user_id: _Optional[str] = ..., agent_execution_id: _Optional[str] = ..., entity_type: _Optional[str] = ..., entity_id: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., request_id: _Optional[str] = ..., hash: _Optional[str] = ...) -> None: ...

class ListCasesRequest(_message.Message):
    __slots__ = ("status", "priority", "assigned_to", "limit", "offset")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_TO_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    status: _enums_pb2.CaseStatus
    priority: _enums_pb2.Severity
    assigned_to: str
    limit: int
    offset: int
    def __init__(self, status: _Optional[_Union[_enums_pb2.CaseStatus, str]] = ..., priority: _Optional[_Union[_enums_pb2.Severity, str]] = ..., assigned_to: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class ListCasesResponse(_message.Message):
    __slots__ = ("total", "items")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    total: int
    items: _containers.RepeatedCompositeFieldContainer[CaseSummary]
    def __init__(self, total: _Optional[int] = ..., items: _Optional[_Iterable[_Union[CaseSummary, _Mapping]]] = ...) -> None: ...

class GetCaseRequest(_message.Message):
    __slots__ = ("case_id",)
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    def __init__(self, case_id: _Optional[str] = ...) -> None: ...

class GetCaseResponse(_message.Message):
    __slots__ = ("detail",)
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    detail: CaseDetail
    def __init__(self, detail: _Optional[_Union[CaseDetail, _Mapping]] = ...) -> None: ...

class AssignCaseRequest(_message.Message):
    __slots__ = ("case_id", "assignee_id")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    ASSIGNEE_ID_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    assignee_id: str
    def __init__(self, case_id: _Optional[str] = ..., assignee_id: _Optional[str] = ...) -> None: ...

class AssignCaseResponse(_message.Message):
    __slots__ = ("summary",)
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    summary: CaseSummary
    def __init__(self, summary: _Optional[_Union[CaseSummary, _Mapping]] = ...) -> None: ...

class TransitionCaseRequest(_message.Message):
    __slots__ = ("case_id", "to_status", "comment", "closure_reason")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    TO_STATUS_FIELD_NUMBER: _ClassVar[int]
    COMMENT_FIELD_NUMBER: _ClassVar[int]
    CLOSURE_REASON_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    to_status: _enums_pb2.CaseStatus
    comment: str
    closure_reason: _enums_pb2.ClosureReason
    def __init__(self, case_id: _Optional[str] = ..., to_status: _Optional[_Union[_enums_pb2.CaseStatus, str]] = ..., comment: _Optional[str] = ..., closure_reason: _Optional[_Union[_enums_pb2.ClosureReason, str]] = ...) -> None: ...

class TransitionCaseResponse(_message.Message):
    __slots__ = ("summary",)
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    summary: CaseSummary
    def __init__(self, summary: _Optional[_Union[CaseSummary, _Mapping]] = ...) -> None: ...

class AddNoteRequest(_message.Message):
    __slots__ = ("case_id", "text")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    text: str
    def __init__(self, case_id: _Optional[str] = ..., text: _Optional[str] = ...) -> None: ...

class AddNoteResponse(_message.Message):
    __slots__ = ("evidence",)
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    evidence: Evidence
    def __init__(self, evidence: _Optional[_Union[Evidence, _Mapping]] = ...) -> None: ...

class StartInvestigationRequest(_message.Message):
    __slots__ = ("case_id",)
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    def __init__(self, case_id: _Optional[str] = ...) -> None: ...

class StartInvestigationResponse(_message.Message):
    __slots__ = ("workflow",)
    WORKFLOW_FIELD_NUMBER: _ClassVar[int]
    workflow: Workflow
    def __init__(self, workflow: _Optional[_Union[Workflow, _Mapping]] = ...) -> None: ...

class GetWorkflowRequest(_message.Message):
    __slots__ = ("case_id",)
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    def __init__(self, case_id: _Optional[str] = ...) -> None: ...

class GetWorkflowResponse(_message.Message):
    __slots__ = ("workflow",)
    WORKFLOW_FIELD_NUMBER: _ClassVar[int]
    workflow: Workflow
    def __init__(self, workflow: _Optional[_Union[Workflow, _Mapping]] = ...) -> None: ...

class ListCaseAuditRequest(_message.Message):
    __slots__ = ("case_id",)
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    def __init__(self, case_id: _Optional[str] = ...) -> None: ...

class ListCaseAuditResponse(_message.Message):
    __slots__ = ("events",)
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    events: _containers.RepeatedCompositeFieldContainer[AuditEvent]
    def __init__(self, events: _Optional[_Iterable[_Union[AuditEvent, _Mapping]]] = ...) -> None: ...

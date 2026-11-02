"""Request and response bodies. Responses are built from ORM objects (from_attributes)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from finrecon_ai.db.enums import (
    ApprovalDecisionType,
    ApprovalRequestStatus,
    CaseStatus,
    ClosureReason,
    Direction,
    EvidenceType,
    ExceptionCategory,
    FindingType,
    ImportBatchStatus,
    PolicyDocumentStatus,
    RecommendationStatus,
    ResolutionType,
    RunStatus,
    Severity,
    SourceType,
)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- auth ---


class LoginIn(In):
    tenant: str = Field(description="tenant slug, e.g. demo")
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class MeOut(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]


# --- data ---


class SourceOut(Out):
    id: uuid.UUID
    name: str
    source_type: SourceType
    ingestion_method: str
    is_active: bool


class ImportBatchOut(Out):
    id: uuid.UUID
    source_id: uuid.UUID
    file_name: str
    status: ImportBatchStatus
    total_records: int
    accepted_records: int
    rejected_records: int
    created_at: datetime


class ImportBatchDetailOut(ImportBatchOut):
    validation_errors: list[dict[str, Any]]


class RunIn(In):
    import_batch_ids: list[uuid.UUID]
    open_cases: bool = True


class RunOut(Out):
    id: uuid.UUID
    status: RunStatus
    engine_version: str
    summary: dict[str, Any]
    created_at: datetime
    cases_opened: int | None = None


# --- cases ---


class CaseSummaryOut(Out):
    id: uuid.UUID
    case_number: str
    title: str
    status: CaseStatus
    priority: Severity
    discrepancy_amount: Decimal | None
    currency: str | None
    assigned_to: uuid.UUID | None
    created_at: datetime


class CaseListOut(BaseModel):
    total: int
    items: list[CaseSummaryOut]


class ExceptionOut(Out):
    id: uuid.UUID
    category: ExceptionCategory
    severity: Severity
    rule_code: str | None
    details: dict[str, Any]


class TransactionOut(Out):
    id: uuid.UUID
    source_type: SourceType | None = None
    source_transaction_id: str | None
    external_reference: str | None
    transaction_date: date
    settlement_date: date | None
    amount: Decimal
    fee_amount: Decimal | None
    currency: str
    direction: Direction
    counterparty: str | None
    merchant: str | None
    description: str | None
    raw_payload: dict[str, Any]


class MatchOut(Out):
    id: uuid.UUID
    classification: str
    strategy: str
    score: Decimal
    explanation: dict[str, Any]


class EvidenceOut(Out):
    id: uuid.UUID
    evidence_type: EvidenceType
    transaction_id: uuid.UUID | None
    policy_document_id: uuid.UUID | None
    related_case_id: uuid.UUID | None
    source_reference: dict[str, Any]
    excerpt: str | None
    agent_execution_id: uuid.UUID | None
    collected_by_user_id: uuid.UUID | None


class FindingOut(Out):
    id: uuid.UUID
    finding_type: FindingType
    statement: str
    confidence: Decimal | None
    evidence_ids: list[uuid.UUID]
    agent_execution_id: uuid.UUID | None
    recorded_by: str | None = None


class RecommendationOut(Out):
    id: uuid.UUID
    version: int
    resolution_type: ResolutionType
    status: RecommendationStatus
    summary: str
    rationale: str
    proposed_action: dict[str, Any]
    confidence: Decimal | None
    execution_result: dict[str, Any] | None
    executed_at: datetime | None


class DecisionOut(Out):
    id: uuid.UUID
    approval_request_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: ApprovalDecisionType
    comments: str | None
    decided_at: datetime


class ApprovalRequestOut(Out):
    id: uuid.UUID
    case_id: uuid.UUID
    recommendation_id: uuid.UUID
    status: ApprovalRequestStatus
    requested_by_user_id: uuid.UUID | None
    created_at: datetime


class CaseDetailOut(CaseSummaryOut):
    closure_reason: ClosureReason | None
    resolved_at: datetime | None
    closed_at: datetime | None
    exception: ExceptionOut
    transactions: list[TransactionOut]
    matches: list[MatchOut]
    findings: list[FindingOut]
    evidence: list[EvidenceOut]
    recommendations: list[RecommendationOut]
    approval_requests: list[ApprovalRequestOut]
    decisions: list[DecisionOut]


class AssignIn(In):
    assignee_id: uuid.UUID


class TransitionIn(In):
    to_status: CaseStatus
    comment: str | None = None
    closure_reason: ClosureReason | None = None


class NoteIn(In):
    text: str = Field(min_length=1, max_length=5000)


class WorkflowOut(BaseModel):
    case_id: uuid.UUID
    started: bool
    paused_for_approval: bool
    running: bool
    round: int | None = None
    approval_request_id: str | None = None
    recommendation_id: str | None = None
    error: str | None = None
    rejected_output: list[str] = []
    execution_result: dict[str, Any] | None = None


# --- approvals ---


class PendingApprovalOut(BaseModel):
    request: ApprovalRequestOut
    case_number: str
    case_title: str
    recommendation: RecommendationOut


class DecisionIn(In):
    decision: ApprovalDecisionType
    comment: str | None = Field(default=None, max_length=5000)
    evidence_considered_ids: list[uuid.UUID] | None = None


# --- policies and audit ---


class PolicyOut(Out):
    id: uuid.UUID
    title: str
    file_name: str
    version: int
    status: PolicyDocumentStatus
    chunk_count: int
    error: str | None
    created_at: datetime


class PolicyHitOut(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    version: int
    section: str
    page: int | None
    text: str
    score: float


class AuditEventOut(Out):
    id: int
    occurred_at: datetime
    event_type: str
    actor_type: str
    actor_user_id: uuid.UUID | None
    agent_execution_id: uuid.UUID | None
    entity_type: str
    entity_id: str | None
    payload: dict[str, Any]
    request_id: str | None
    hash: str


class ChainCheckOut(BaseModel):
    intact: bool
    first_bad_event_id: int | None
    events_checked: int

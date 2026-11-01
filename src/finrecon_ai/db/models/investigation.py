import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from finrecon_ai.db.base import (
    Base,
    CurrencyCode,
    Money,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    str_enum,
)
from finrecon_ai.db.enums import (
    CaseStatus,
    ClosureReason,
    EvidenceType,
    FindingType,
    RecommendationStatus,
    ResolutionType,
    Severity,
)


class InvestigationCase(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "investigation_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_number"),
        CheckConstraint("(status = 'closed') = (closure_reason IS NOT NULL)", name="closed_has_reason"),
    )

    case_number: Mapped[str] = mapped_column(String(50))
    # One case per qualifying exception (FR-05); related transactions come via exception_transactions.
    exception_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_exceptions.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[CaseStatus] = mapped_column(str_enum(CaseStatus), server_default=CaseStatus.NEW.value, index=True)
    priority: Mapped[Severity] = mapped_column(str_enum(Severity))
    title: Mapped[str] = mapped_column(String(300))
    discrepancy_amount: Mapped[Money | None]
    currency: Mapped[CurrencyCode | None]
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # LangGraph thread that drives this case's workflow and checkpoints.
    workflow_thread_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reason: Mapped[ClosureReason | None] = mapped_column(str_enum(ClosureReason))


class InvestigationEvidence(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "investigation_evidence"
    __table_args__ = (
        CheckConstraint(
            "(collected_by_user_id IS NOT NULL) OR (agent_execution_id IS NOT NULL)",
            name="has_collector",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[EvidenceType] = mapped_column(str_enum(EvidenceType))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transactions.id", ondelete="RESTRICT"))
    policy_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="RESTRICT")
    )
    related_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigation_cases.id", ondelete="SET NULL"))
    # Citation detail: section, page, Qdrant point id, file path.
    source_reference: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    excerpt: Mapped[str | None] = mapped_column(Text)
    collected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationFinding(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "investigation_findings"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        # A verified fact must point at evidence.
        CheckConstraint(
            "finding_type <> 'verified_fact' OR cardinality(evidence_ids) > 0",
            name="fact_requires_evidence",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True)
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="SET NULL")
    )
    finding_type: Mapped[FindingType] = mapped_column(str_enum(FindingType))
    statement: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResolutionRecommendation(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "resolution_recommendations"
    __table_args__ = (
        UniqueConstraint("case_id", "version"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    resolution_type: Mapped[ResolutionType] = mapped_column(str_enum(ResolutionType))
    status: Mapped[RecommendationStatus] = mapped_column(
        str_enum(RecommendationStatus), server_default=RecommendationStatus.DRAFT.value
    )
    summary: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    # Structured action the deterministic service would apply after approval.
    proposed_action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="SET NULL")
    )
    # Guards against executing the same approved action twice on retry.
    execution_idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

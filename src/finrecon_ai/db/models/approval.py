import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from finrecon_ai.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from finrecon_ai.db.enums import ApprovalDecisionType, ApprovalRequestStatus, RoleName


class ApprovalRequest(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        # At most one open approval per recommendation.
        Index(
            "uq_approval_requests_pending_recommendation",
            "recommendation_id",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolution_recommendations.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[ApprovalRequestStatus] = mapped_column(
        str_enum(ApprovalRequestStatus), server_default=ApprovalRequestStatus.PENDING.value, index=True
    )
    required_role: Mapped[RoleName] = mapped_column(str_enum(RoleName), server_default=RoleName.MANAGER.value)
    # Who started the workflow that produced the recommendation; they may not decide it (separation of duties).
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    requested_by_agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="SET NULL")
    )
    # LangGraph interrupt location so the workflow can be resumed after the decision (FR-09).
    workflow_thread_id: Mapped[str] = mapped_column(String(200))
    workflow_checkpoint_id: Mapped[str | None] = mapped_column(String(200))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalDecision(UUIDPrimaryKeyMixin, TenantMixin, Base):
    """Immutable record of a reviewer's decision."""

    __tablename__ = "approval_decisions"

    # One decision per request: a second reviewer cannot overwrite the first.
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), unique=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[ApprovalDecisionType] = mapped_column(str_enum(ApprovalDecisionType))
    comments: Mapped[str | None] = mapped_column(Text)
    evidence_considered_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}"
    )
    # Snapshot of the recommendation as the reviewer saw it.
    original_recommendation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    final_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

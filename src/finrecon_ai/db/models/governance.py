import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from finrecon_ai.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from finrecon_ai.db.enums import ActorType, AgentExecutionStatus, AgentName, PolicyDocumentStatus


class PolicyDocument(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Policy/procedure file metadata. Chunks and embeddings live in Qdrant (FR-07)."""

    __tablename__ = "policy_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "file_sha256"),)

    title: Mapped[str] = mapped_column(String(300))
    file_name: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(150))
    file_sha256: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[PolicyDocumentStatus] = mapped_column(
        str_enum(PolicyDocumentStatus), server_default=PolicyDocumentStatus.UPLOADED.value
    )
    qdrant_collection: Mapped[str | None] = mapped_column(String(200))
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentExecutionRecord(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "agent_execution_records"

    agent_name: Mapped[AgentName] = mapped_column(str_enum(AgentName), index=True)
    status: Mapped[AgentExecutionStatus] = mapped_column(
        str_enum(AgentExecutionStatus), server_default=AgentExecutionStatus.RUNNING.value
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigation_cases.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="SET NULL"))
    parent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="SET NULL")
    )
    workflow_thread_id: Mapped[str | None] = mapped_column(String(200), index=True)
    workflow_version: Mapped[str] = mapped_column(String(50))
    langsmith_run_id: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(100))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # [{"tool": "get_transaction", "args": {...}, "ok": true, "duration_ms": 12}]
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(TenantMixin, Base):
    """Append-only (enforced by DB trigger) and hash-chained per tenant (FR-12)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_execution_records.id", ondelete="RESTRICT")
    )
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    request_id: Mapped[str | None] = mapped_column(String(100))
    ip_address: Mapped[str | None] = mapped_column(INET)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

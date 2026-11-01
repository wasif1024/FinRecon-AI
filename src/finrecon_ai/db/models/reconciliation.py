import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    ExceptionCategory,
    ExceptionStatus,
    MatchClassification,
    MatchStrategy,
    RunStatus,
    Severity,
)

# A match can group several transactions (e.g. one bank line vs. many gateway payments).
transaction_match_items = Table(
    "transaction_match_items",
    Base.metadata,
    Column("match_id", ForeignKey("transaction_matches.id", ondelete="CASCADE"), primary_key=True),
    Column("transaction_id", ForeignKey("transactions.id", ondelete="RESTRICT"), primary_key=True, index=True),
)

exception_transactions = Table(
    "exception_transactions",
    Base.metadata,
    Column("exception_id", ForeignKey("reconciliation_exceptions.id", ondelete="CASCADE"), primary_key=True),
    Column("transaction_id", ForeignKey("transactions.id", ondelete="RESTRICT"), primary_key=True, index=True),
)


class ReconciliationRun(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), server_default=RunStatus.PENDING.value)
    # Snapshot of strategies, tolerances and date windows so every run is reproducible.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    engine_version: Mapped[str] = mapped_column(String(50))
    # Import batches included in this run: [uuid, ...]
    import_batch_ids: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    # Per-currency counts and totals; never summed across currencies.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String)


class TransactionMatch(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "transaction_matches"
    __table_args__ = (CheckConstraint("score >= 0 AND score <= 1", name="score_range"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True)
    classification: Mapped[MatchClassification] = mapped_column(str_enum(MatchClassification), index=True)
    strategy: Mapped[MatchStrategy] = mapped_column(str_enum(MatchStrategy))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    amount_difference: Mapped[Money | None]
    currency: Mapped[CurrencyCode | None]
    # Which rules fired and with what values, so the decision is explainable (FR-03).
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationException(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_exceptions"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="RESTRICT"), index=True)
    match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transaction_matches.id", ondelete="SET NULL"))
    category: Mapped[ExceptionCategory] = mapped_column(str_enum(ExceptionCategory), index=True)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity))
    status: Mapped[ExceptionStatus] = mapped_column(
        str_enum(ExceptionStatus), server_default=ExceptionStatus.OPEN.value, index=True
    )
    discrepancy_amount: Mapped[Money | None]
    currency: Mapped[CurrencyCode | None]
    # Rule code for anomalies (FR-08) and any category-specific detail.
    rule_code: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

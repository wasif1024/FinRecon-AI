import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    Direction,
    ImportBatchStatus,
    IngestionMethod,
    SourceType,
    TransactionStatus,
)


class FinancialSource(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "financial_sources"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType))
    ingestion_method: Mapped[IngestionMethod] = mapped_column(str_enum(IngestionMethod))
    # Column mappings, date formats, API settings. Never store secrets here.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    is_active: Mapped[bool] = mapped_column(server_default="true")


class ImportBatch(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        # Re-uploading the same file to the same source is idempotent.
        UniqueConstraint("tenant_id", "source_id", "file_sha256"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financial_sources.id", ondelete="RESTRICT"), index=True)
    file_name: Mapped[str] = mapped_column(String(500))
    file_format: Mapped[IngestionMethod] = mapped_column(str_enum(IngestionMethod))
    file_sha256: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ImportBatchStatus] = mapped_column(
        str_enum(ImportBatchStatus), server_default=ImportBatchStatus.PENDING.value
    )
    total_records: Mapped[int] = mapped_column(Integer, server_default="0")
    accepted_records: Mapped[int] = mapped_column(Integer, server_default="0")
    rejected_records: Mapped[int] = mapped_column(Integer, server_default="0")
    # [{"row": 12, "field": "amount", "error": "not a decimal", "raw": {...}}]
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Transaction(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Normalized transaction model (FR-02). raw_payload keeps the original record."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "source_row_number"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("fee_amount IS NULL OR fee_amount >= 0", name="fee_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        Index("ix_transactions_tenant_reference", "tenant_id", "external_reference"),
        Index("ix_transactions_tenant_amount_date", "tenant_id", "currency", "amount", "transaction_date"),
        Index("ix_transactions_tenant_fingerprint", "tenant_id", "fingerprint"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("financial_sources.id", ondelete="RESTRICT"), index=True)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True
    )
    source_row_number: Mapped[int] = mapped_column(BigInteger)
    # This record's own ID in its source system.
    source_transaction_id: Mapped[str | None] = mapped_column(String(200))
    # Points at the upstream counterpart: bank -> settlement ID, settlement -> payment ID.
    external_reference: Mapped[str | None] = mapped_column(String(200))
    transaction_date: Mapped[date] = mapped_column(Date)
    # Full timestamp when the source provides one; used for duplicate detection (FR-04).
    transaction_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_date: Mapped[date | None] = mapped_column(Date)
    # Absolute amount; sign is carried by direction.
    amount: Mapped[Money]
    # Processing fee reported by the source, if any.
    fee_amount: Mapped[Money | None]
    currency: Mapped[CurrencyCode]
    direction: Mapped[Direction] = mapped_column(str_enum(Direction))
    counterparty: Mapped[str | None] = mapped_column(String(300))
    merchant: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TransactionStatus] = mapped_column(str_enum(TransactionStatus))
    # Hash of normalized reference/amount/currency/counterparty/date, used for duplicate detection.
    fingerprint: Mapped[str] = mapped_column(String(64))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

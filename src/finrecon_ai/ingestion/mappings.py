"""How each source's columns map onto the normalized transaction model (FR-02).

Mappings are stored in financial_sources.config, so a new source layout needs no code change.

Reference convention used across sources:
- source_transaction_id: the record's own ID in its system.
- external_reference: the ID of its upstream counterpart.
  bank.reference -> settlement.settlement_id, settlement.payment_id -> gateway.id.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from finrecon_ai.db.enums import Direction, SourceType, TransactionStatus

NormalizedField = Literal[
    "source_transaction_id",
    "external_reference",
    "transaction_date",
    "settlement_date",
    "amount",
    "fee_amount",
    "currency",
    "direction",
    "counterparty",
    "merchant",
    "description",
    "status",
]

REQUIRED_FIELDS: tuple[NormalizedField, ...] = ("transaction_date", "amount", "currency")


class SourceMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # normalized field -> source column / JSON key
    columns: dict[NormalizedField, str]
    # strptime format for date columns; None means ISO 8601 (date or datetime).
    date_format: str | None = None
    # "signed": negative amount is a debit. "positive": direction comes from a column or the default.
    amount_sign: Literal["signed", "positive"] = "positive"
    default_direction: Direction | None = None
    direction_map: dict[str, Direction] = {}
    status_map: dict[str, TransactionStatus] = {}
    default_status: TransactionStatus | None = None
    # For JSON files that wrap records, e.g. {"data": [...]}.
    json_records_key: str | None = None

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        missing = [f for f in REQUIRED_FIELDS if f not in self.columns]
        if missing:
            raise ValueError(f"mapping is missing required fields: {missing}")
        if self.amount_sign == "positive" and "direction" not in self.columns and self.default_direction is None:
            raise ValueError("positive amounts need a direction column or default_direction")
        if "status" not in self.columns and self.default_status is None:
            raise ValueError("mapping needs a status column or default_status")
        return self

    def required_source_columns(self) -> set[str]:
        required: list[NormalizedField] = list(REQUIRED_FIELDS)
        if self.default_status is None:
            required.append("status")
        return {self.columns[f] for f in required}


DEFAULT_MAPPINGS: dict[SourceType, SourceMapping] = {
    SourceType.BANK_STATEMENT: SourceMapping(
        columns={
            "source_transaction_id": "transaction_id",
            "external_reference": "reference",
            "transaction_date": "booking_date",
            "settlement_date": "value_date",
            "amount": "amount",
            "currency": "currency",
            "counterparty": "counterparty",
            "description": "description",
        },
        amount_sign="signed",
        default_status=TransactionStatus.SETTLED,
    ),
    SourceType.PAYMENT_GATEWAY: SourceMapping(
        columns={
            "source_transaction_id": "id",
            "external_reference": "order_reference",
            "transaction_date": "created_at",
            "amount": "amount",
            "fee_amount": "fee",
            "currency": "currency",
            "counterparty": "customer_name",
            "merchant": "merchant_name",
            "description": "description",
            "status": "status",
        },
        default_direction=Direction.CREDIT,
        status_map={
            "succeeded": TransactionStatus.SETTLED,
            "pending": TransactionStatus.PENDING,
            "failed": TransactionStatus.FAILED,
            "refunded": TransactionStatus.REFUNDED,
        },
        json_records_key="data",
    ),
    SourceType.MERCHANT_SETTLEMENT: SourceMapping(
        columns={
            "source_transaction_id": "settlement_id",
            "external_reference": "payment_id",
            "transaction_date": "settlement_date",
            "settlement_date": "settlement_date",
            "amount": "net_amount",
            "fee_amount": "fee_amount",
            "currency": "currency",
            "merchant": "merchant",
        },
        date_format="%d/%m/%Y",
        default_direction=Direction.CREDIT,
        default_status=TransactionStatus.SETTLED,
    ),
}

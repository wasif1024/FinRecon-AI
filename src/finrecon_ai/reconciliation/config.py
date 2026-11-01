"""Reconciliation rules. A run stores a snapshot of this config so its results are reproducible (FR-03).

Tuples rather than sets keep the JSON snapshot (and its hash) stable.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finrecon_ai.db.enums import ExceptionCategory, SourceType, TransactionStatus

KeyField = Literal["source_transaction_id", "external_reference"]
AmountBasis = Literal["amount", "net_of_fee"]  # net_of_fee = amount - fee_amount


class LegConfig(BaseModel):
    """Match one source (left) against the source it flows into (right)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    left_source: SourceType
    right_source: SourceType
    left_key: KeyField
    right_key: KeyField
    left_amount: AmountBasis = "amount"
    right_amount: AmountBasis = "amount"
    # Allowed (right date - left date) in calendar days.
    min_lag_days: int = 0
    max_lag_days: int
    amount_tolerance: Decimal = Decimal("0")
    # When amounts differ, compare fee_amount on both sides to tell a fee discrepancy apart.
    compare_fees: bool = False
    compare_merchant: bool = False
    # Only left records in these statuses are expected to have a counterpart.
    left_statuses: tuple[TransactionStatus, ...] = (TransactionStatus.SETTLED,)
    # Fallback pass for records whose reference did not join: same amount + currency + date window.
    fallback_matching: bool = True
    # Fallback only considers right records from these counterparties (empty = any).
    fallback_right_counterparties: tuple[str, ...] = ()
    left_unmatched_category: ExceptionCategory = ExceptionCategory.UNMATCHED
    right_unmatched_category: ExceptionCategory = ExceptionCategory.UNMATCHED


class ReconciliationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    legs: tuple[LegConfig, ...]
    # Same amount, currency, counterparty and reference within this window = potential duplicate (FR-04).
    duplicate_window_minutes: int = Field(default=10, ge=0)
    high_value_threshold: Decimal = Decimal("1000")


DEFAULT_CONFIG = ReconciliationConfig(
    legs=(
        LegConfig(
            name="gateway_to_settlement",
            left_source=SourceType.PAYMENT_GATEWAY,
            right_source=SourceType.MERCHANT_SETTLEMENT,
            left_key="source_transaction_id",
            right_key="external_reference",
            left_amount="net_of_fee",
            right_amount="amount",
            max_lag_days=5,
            compare_fees=True,
            compare_merchant=True,
            fallback_matching=False,  # settlements always carry the payment ID
            left_unmatched_category=ExceptionCategory.MISSING_SETTLEMENT,
        ),
        LegConfig(
            name="settlement_to_bank",
            left_source=SourceType.MERCHANT_SETTLEMENT,
            right_source=SourceType.BANK_STATEMENT,
            left_key="source_transaction_id",
            right_key="external_reference",
            max_lag_days=4,
            fallback_right_counterparties=("paygate payments ltd",),
        ),
    ),
)

ENGINE_VERSION = "1.0.0"

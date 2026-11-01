"""Turn one raw source record into the common transaction model. Pure functions, no I/O."""

import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from finrecon_ai.db.enums import Direction, TransactionStatus
from finrecon_ai.ingestion.mappings import NormalizedField, SourceMapping

MAX_DECIMAL_PLACES = 4  # matches NUMERIC(19, 4)
MAX_INTEGER_DIGITS = 15

# ISO 4217 codes accepted by the MVP. Extend as new sources are onboarded.
SUPPORTED_CURRENCIES = frozenset(
    "AED AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD INR JPY KRW MXN NOK NZD PKR PLN SAR SEK SGD TRY USD ZAR".split()
)


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class FieldError:
    field: str
    column: str | None
    error: str


class RecordRejected(Exception):
    def __init__(self, errors: list[FieldError]):
        super().__init__("; ".join(f"{e.field}: {e.error}" for e in errors))
        self.errors = errors


@dataclass(frozen=True)
class NormalizedTransaction:
    source_transaction_id: str | None
    external_reference: str | None
    transaction_date: date
    transaction_timestamp: datetime | None
    settlement_date: date | None
    amount: Decimal
    fee_amount: Decimal | None
    currency: str
    direction: Direction
    counterparty: str | None
    merchant: str | None
    description: str | None
    status: TransactionStatus


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        # Floats have already lost precision; JSON must be parsed with parse_float=Decimal.
        raise NormalizationError(f"expected a decimal, got {type(value).__name__}")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise NormalizationError(f"not a valid decimal: {value!r}") from None
    if not result.is_finite():
        raise NormalizationError(f"not a finite number: {value!r}")
    exponent = result.as_tuple().exponent
    assert isinstance(exponent, int)
    if -exponent > MAX_DECIMAL_PLACES:
        raise NormalizationError(f"more than {MAX_DECIMAL_PLACES} decimal places: {value!r}")
    if result.adjusted() >= MAX_INTEGER_DIGITS:
        raise NormalizationError(f"amount too large: {value!r}")
    return result


def parse_date(value: Any, fmt: str | None) -> tuple[date, datetime | None]:
    """Returns the date, plus the full timestamp when the value carries a time."""
    text = str(value).strip()
    try:
        if fmt:
            parsed = datetime.strptime(text, fmt)
            return parsed.date(), None
        if len(text) == 10:
            return date.fromisoformat(text), None
        parsed = datetime.fromisoformat(text)
    except ValueError:
        expected = fmt or "ISO 8601"
        raise NormalizationError(f"not a valid date ({expected}): {value!r}") from None
    if parsed.tzinfo is None:
        raise NormalizationError(f"timestamp has no timezone: {value!r}")
    parsed = parsed.astimezone(UTC)
    return parsed.date(), parsed


def parse_currency(value: Any) -> str:
    code = str(value).strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise NormalizationError(f"unsupported currency code: {value!r}")
    return code


def normalize_record(raw: Mapping[str, Any], mapping: SourceMapping) -> NormalizedTransaction:
    """Raises RecordRejected listing every problem in the record, not just the first."""
    errors: list[FieldError] = []

    def parse[T](field: NormalizedField, fn: Callable[[Any], T], required: bool = False) -> T | None:
        column = mapping.columns.get(field)
        value = _clean(raw.get(column)) if column else None
        if value is None:
            if required:
                errors.append(FieldError(field, column, "required value is missing"))
            return None
        try:
            return fn(value)
        except NormalizationError as exc:
            errors.append(FieldError(field, column, str(exc)))
            return None

    def text(value: Any) -> str:
        return str(value).strip()

    dates = parse("transaction_date", lambda v: parse_date(v, mapping.date_format), required=True)
    settlement = parse("settlement_date", lambda v: parse_date(v, mapping.date_format)[0])
    signed_amount = parse("amount", parse_decimal, required=True)
    fee = parse("fee_amount", parse_decimal)
    currency = parse("currency", parse_currency, required=True)
    direction = _direction(raw, mapping, signed_amount, errors)
    status = _status(raw, mapping, errors)

    if fee is not None and fee < 0:
        errors.append(FieldError("fee_amount", mapping.columns.get("fee_amount"), "fee cannot be negative"))

    if errors:
        raise RecordRejected(errors)
    assert dates and signed_amount is not None and currency and direction and status

    return NormalizedTransaction(
        source_transaction_id=parse("source_transaction_id", text),
        external_reference=parse("external_reference", text),
        transaction_date=dates[0],
        transaction_timestamp=dates[1],
        settlement_date=settlement,
        amount=abs(signed_amount),
        fee_amount=fee,
        currency=currency,
        direction=direction,
        counterparty=parse("counterparty", text),
        merchant=parse("merchant", text),
        description=parse("description", text),
        status=status,
    )


def _direction(
    raw: Mapping[str, Any], mapping: SourceMapping, amount: Decimal | None, errors: list[FieldError]
) -> Direction | None:
    column = mapping.columns.get("amount")
    if amount is not None and amount == 0:
        errors.append(FieldError("amount", column, "amount cannot be zero"))
        return None
    if mapping.amount_sign == "signed":
        if amount is None:
            return None
        return Direction.DEBIT if amount < 0 else Direction.CREDIT

    if amount is not None and amount < 0:
        errors.append(FieldError("amount", column, "negative amount in a positive-amount source"))
        return None
    direction_column = mapping.columns.get("direction")
    value = _clean(raw.get(direction_column)) if direction_column else None
    if value is None:
        return mapping.default_direction
    key = str(value).strip().lower()
    if key in mapping.direction_map:
        return mapping.direction_map[key]
    try:
        return Direction(key)
    except ValueError:
        errors.append(FieldError("direction", direction_column, f"unknown direction: {value!r}"))
        return None


def _status(raw: Mapping[str, Any], mapping: SourceMapping, errors: list[FieldError]) -> TransactionStatus | None:
    column = mapping.columns.get("status")
    value = _clean(raw.get(column)) if column else None
    if value is None:
        if mapping.default_status is None:
            errors.append(FieldError("status", column, "required value is missing"))
        return mapping.default_status
    key = str(value).strip().lower()
    if key in mapping.status_map:
        return mapping.status_map[key]
    try:
        return TransactionStatus(key)
    except ValueError:
        errors.append(FieldError("status", column, f"unknown status: {value!r}"))
        return None


def _clean(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def fingerprint(source_id: uuid.UUID, txn: NormalizedTransaction) -> str:
    """Stable hash of the fields that make two records from the same source look identical (FR-04)."""
    parts = [
        str(source_id),
        (txn.external_reference or "").upper(),
        f"{txn.amount.normalize():f}",
        txn.currency,
        txn.direction.value,
        " ".join((txn.counterparty or "").lower().split()),
        txn.transaction_date.isoformat(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()

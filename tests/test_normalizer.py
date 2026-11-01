from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from finrecon_ai.db.enums import Direction, SourceType, TransactionStatus
from finrecon_ai.ingestion.mappings import DEFAULT_MAPPINGS, SourceMapping
from finrecon_ai.ingestion.normalizer import (
    NormalizationError,
    RecordRejected,
    fingerprint,
    normalize_record,
    parse_currency,
    parse_date,
    parse_decimal,
)

BANK = DEFAULT_MAPPINGS[SourceType.BANK_STATEMENT]
GATEWAY = DEFAULT_MAPPINGS[SourceType.PAYMENT_GATEWAY]
SETTLEMENT = DEFAULT_MAPPINGS[SourceType.MERCHANT_SETTLEMENT]


def bank_row(**overrides):
    row = {
        "transaction_id": "BNK-1",
        "booking_date": "2026-10-06",
        "value_date": "2026-10-07",
        "description": "PAYGATE SETTLEMENT STL-000001",
        "reference": "STL-000001",
        "amount": "287.80",
        "currency": "EUR",
        "counterparty": "PayGate Payments Ltd",
    }
    return row | overrides


class TestParseDecimal:
    @pytest.mark.parametrize("value, expected", [("10.50", "10.50"), (" 7 ", "7"), (Decimal("0.0001"), "0.0001"),
                                                 (12, "12"), ("-3.25", "-3.25")])
    def test_valid(self, value, expected):
        assert parse_decimal(value) == Decimal(expected)

    @pytest.mark.parametrize("value", ["12.3.4", "abc", "NaN", "Infinity", "1,000.00", "0.00001", "1e20"])
    def test_invalid(self, value):
        with pytest.raises(NormalizationError):
            parse_decimal(value)

    def test_rejects_float(self):
        with pytest.raises(NormalizationError, match="float"):
            parse_decimal(0.1)


class TestParseDate:
    def test_iso_date(self):
        assert parse_date("2026-10-06", None) == (date(2026, 10, 6), None)

    def test_iso_timestamp_converted_to_utc(self):
        day, ts = parse_date("2026-10-15T23:30:00-02:00", None)
        assert ts == datetime(2026, 10, 16, 1, 30, tzinfo=UTC)
        assert day == date(2026, 10, 16)

    def test_custom_format(self):
        assert parse_date("05/10/2026", "%d/%m/%Y") == (date(2026, 10, 5), None)

    @pytest.mark.parametrize("value", ["2026-13-45", "yesterday", "2026-10-15T10:00:00"])
    def test_invalid(self, value):
        with pytest.raises(NormalizationError):
            parse_date(value, None)


class TestParseCurrency:
    def test_normalizes_case(self):
        assert parse_currency(" usd ") == "USD"

    @pytest.mark.parametrize("value", ["US", "XXX", "dollars"])
    def test_rejects_unknown(self, value):
        with pytest.raises(NormalizationError):
            parse_currency(value)


class TestNormalizeRecord:
    def test_bank_credit(self):
        txn = normalize_record(bank_row(), BANK)
        assert txn.amount == Decimal("287.80")
        assert txn.direction is Direction.CREDIT
        assert txn.status is TransactionStatus.SETTLED
        assert txn.source_transaction_id == "BNK-1"
        assert txn.external_reference == "STL-000001"
        assert txn.settlement_date == date(2026, 10, 7)

    def test_signed_negative_is_debit_with_absolute_amount(self):
        txn = normalize_record(bank_row(amount="-25.00"), BANK)
        assert txn.direction is Direction.DEBIT
        assert txn.amount == Decimal("25.00")

    def test_blank_optional_fields_become_none(self):
        txn = normalize_record(bank_row(reference="  ", description=""), BANK)
        assert txn.external_reference is None and txn.description is None

    def test_reports_every_error_at_once(self):
        with pytest.raises(RecordRejected) as exc:
            normalize_record(bank_row(amount="12.3.4", booking_date="2026-13-45", currency=""), BANK)
        assert {e.field for e in exc.value.errors} == {"amount", "transaction_date", "currency"}

    def test_zero_amount_rejected(self):
        with pytest.raises(RecordRejected, match="zero"):
            normalize_record(bank_row(amount="0.00"), BANK)

    def test_gateway_maps_status_and_keeps_timestamp(self):
        raw = {"id": "pg_1", "order_reference": "ORD-1", "created_at": "2026-10-15T22:38:30Z",
               "amount": Decimal("768.51"), "fee": Decimal("22.59"), "currency": "GBP", "status": "succeeded"}
        txn = normalize_record(raw, GATEWAY)
        assert txn.status is TransactionStatus.SETTLED
        assert txn.direction is Direction.CREDIT
        assert txn.fee_amount == Decimal("22.59")
        assert txn.transaction_timestamp == datetime(2026, 10, 15, 22, 38, 30, tzinfo=UTC)

    def test_unknown_status_rejected(self):
        raw = {"id": "pg_1", "created_at": "2026-10-15T22:38:30Z", "amount": Decimal("1"), "currency": "USD",
               "status": "teleported"}
        with pytest.raises(RecordRejected, match="unknown status"):
            normalize_record(raw, GATEWAY)

    def test_negative_amount_rejected_for_positive_source(self):
        raw = {"settlement_id": "STL-1", "settlement_date": "05/10/2026", "net_amount": "-5.00", "currency": "USD"}
        with pytest.raises(RecordRejected, match="negative"):
            normalize_record(raw, SETTLEMENT)


class TestMapping:
    def test_requires_core_fields(self):
        with pytest.raises(ValueError, match="missing required"):
            SourceMapping(columns={"amount": "amt"}, default_direction=Direction.CREDIT,
                          default_status=TransactionStatus.SETTLED)

    def test_defaults_survive_json_round_trip(self):
        for mapping in DEFAULT_MAPPINGS.values():
            assert SourceMapping.model_validate(mapping.model_dump(mode="json")) == mapping


def test_fingerprint_ignores_case_and_spacing():
    import uuid

    source = uuid.uuid4()
    a = normalize_record(bank_row(counterparty="PayGate  Payments Ltd", reference="stl-000001"), BANK)
    b = normalize_record(bank_row(transaction_id="BNK-2", amount="287.8"), BANK)
    assert fingerprint(source, a) == fingerprint(source, b)
    assert fingerprint(uuid.uuid4(), a) != fingerprint(source, a)

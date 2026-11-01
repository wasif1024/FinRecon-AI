import random
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from finrecon_ai.db.enums import (
    Direction,
    ExceptionCategory,
    MatchClassification,
    MatchStrategy,
    Severity,
    SourceType,
    TransactionStatus,
)
from finrecon_ai.reconciliation.config import DEFAULT_CONFIG, LegConfig, ReconciliationConfig
from finrecon_ai.reconciliation.engine import Txn, reconcile

SOURCE_IDS = {s: uuid.uuid4() for s in SourceType}
DAY = date(2026, 10, 5)


def txn(source_type: SourceType, own_id: str, reference: str | None, amount: str, *, fee: str | None = None,
        currency: str = "USD", day: date = DAY, counterparty: str | None = None, merchant: str | None = "Shop",
        status: TransactionStatus = TransactionStatus.SETTLED, direction: Direction = Direction.CREDIT,
        timestamp: datetime | None = None) -> Txn:
    return Txn(id=uuid.uuid4(), source_id=SOURCE_IDS[source_type], source_type=source_type,
               source_transaction_id=own_id, external_reference=reference, transaction_date=day,
               transaction_timestamp=timestamp, amount=Decimal(amount),
               fee_amount=Decimal(fee) if fee else None, currency=currency, direction=direction,
               counterparty=counterparty, merchant=merchant, status=status)


def payment(own_id="pg_1", amount="100.00", fee="3.20", **kw):
    return txn(SourceType.PAYMENT_GATEWAY, own_id, "ORD-1", amount, fee=fee, counterparty="Ava Khan", **kw)


def settlement(own_id="STL-1", payment_id="pg_1", amount="96.80", fee="3.20", day=DAY + timedelta(days=2), **kw):
    return txn(SourceType.MERCHANT_SETTLEMENT, own_id, payment_id, amount, fee=fee, day=day, **kw)


def bank(own_id="BNK-1", reference="STL-1", amount="96.80", day=DAY + timedelta(days=3), **kw):
    kw.setdefault("counterparty", "PayGate Payments Ltd")
    return txn(SourceType.BANK_STATEMENT, own_id, reference, amount, day=day, merchant=None, **kw)


def run(*txns):
    return reconcile(txns, DEFAULT_CONFIG)


def categories(result):
    return sorted(e.category for e in result.exceptions)


def test_clean_chain_fully_matched():
    result = run(payment(), settlement(), bank())
    assert [m.classification for m in result.matches] == [MatchClassification.MATCHED] * 2
    assert all(m.score == 1 and m.strategy is MatchStrategy.EXACT_REFERENCE for m in result.matches)
    assert result.exceptions == []


def test_fee_discrepancy_explained_by_fee_difference():
    result = run(payment(), settlement(amount="95.30", fee="4.70"), bank(amount="95.30"))
    [exc] = result.exceptions
    assert exc.category is ExceptionCategory.FEE_DISCREPANCY
    assert exc.discrepancy_amount == Decimal("1.50")
    fee_rule = next(r for r in result.matches[0].explanation["rules"] if r["rule"] == "fee")
    assert fee_rule["explains_amount_difference"] is True


def test_amount_mismatch_on_bank():
    result = run(payment(), settlement(), bank(amount="96.00"))
    assert categories(result) == [ExceptionCategory.AMOUNT_MISMATCH]
    assert result.exceptions[0].discrepancy_amount == Decimal("0.80")


def test_tolerance_turns_small_difference_into_match():
    legs = tuple(replace_leg(leg, amount_tolerance=Decimal("1.00")) for leg in DEFAULT_CONFIG.legs)
    result = reconcile([payment(), settlement(), bank(amount="96.00")], ReconciliationConfig(legs=legs))
    bank_match = result.matches[1]
    assert bank_match.classification is MatchClassification.MATCHED
    assert bank_match.strategy is MatchStrategy.TOLERANCE
    assert result.exceptions == []


def test_currency_mismatch_is_never_compared_on_amount():
    result = run(payment(), settlement(), bank(currency="EUR"))
    [exc] = result.exceptions
    assert exc.category is ExceptionCategory.CURRENCY_MISMATCH
    assert exc.severity is Severity.HIGH
    amount_rule = next(r for r in result.matches[1].explanation["rules"] if r["rule"] == "amount")
    assert "not compared" in amount_rule["note"]


def test_late_bank_credit_is_date_mismatch():
    result = run(payment(), settlement(), bank(day=DAY + timedelta(days=15)))
    assert categories(result) == [ExceptionCategory.DATE_MISMATCH]


def test_wrong_reference_falls_back_to_partial_match():
    result = run(payment(), settlement(), bank(reference="STL-O1"))
    bank_match = result.matches[1]
    assert bank_match.classification is MatchClassification.PARTIALLY_MATCHED
    assert bank_match.strategy is MatchStrategy.AMOUNT_CURRENCY
    assert categories(result) == [ExceptionCategory.PARTIALLY_MATCHED]


def test_fallback_refuses_ambiguous_candidates():
    result = run(payment(), settlement(), bank("BNK-1", reference=None), bank("BNK-2", reference=None))
    left_unmatched = [e for e in result.exceptions if e.details.get("side") == "left"]
    assert len(left_unmatched) == 1
    assert len(left_unmatched[0].details["ambiguous_candidates"]) == 2


def test_fallback_ignores_other_counterparties():
    result = run(payment(), settlement(), bank(reference=None, counterparty="Someone Else"))
    assert len(result.matches) == 1  # only gateway -> settlement
    assert categories(result) == [ExceptionCategory.UNMATCHED, ExceptionCategory.UNMATCHED]


def test_duplicate_bank_credit_is_confirmed_once():
    result = run(payment(), settlement(), bank("BNK-1"), bank("BNK-2"))
    assert categories(result) == [ExceptionCategory.CONFIRMED_DUPLICATE]
    assert [m.classification for m in result.matches].count(MatchClassification.DUPLICATE) == 1


def test_missing_settlement_and_missing_bank_credit():
    result = run(payment("pg_1"), payment("pg_2", amount="50.00"), settlement())
    assert categories(result) == [ExceptionCategory.MISSING_SETTLEMENT, ExceptionCategory.UNMATCHED]


def test_failed_payment_expects_no_settlement():
    result = run(payment(status=TransactionStatus.FAILED))
    assert result.exceptions == []


def test_double_charge_within_window_is_potential_duplicate():
    t0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    result = run(payment("pg_1", timestamp=t0), payment("pg_2", timestamp=t0 + timedelta(minutes=3)),
                 settlement("STL-1", "pg_1"), settlement("STL-2", "pg_2"),
                 bank("BNK-1", "STL-1"), bank("BNK-2", "STL-2"))
    [exc] = result.exceptions
    assert exc.category is ExceptionCategory.POTENTIAL_DUPLICATE
    assert exc.details["source_transaction_ids"] == ["pg_1", "pg_2"]


def test_same_charge_outside_window_is_not_duplicate():
    t0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    result = run(payment("pg_1", timestamp=t0), payment("pg_2", timestamp=t0 + timedelta(hours=2)),
                 settlement("STL-1", "pg_1"), settlement("STL-2", "pg_2"),
                 bank("BNK-1", "STL-1"), bank("BNK-2", "STL-2"))
    assert result.exceptions == []


def test_resent_record_is_confirmed_duplicate():
    first = bank("BNK-1", reference=None, counterparty="Bank charges", amount="25.00", direction=Direction.DEBIT)
    resent = replace(first, id=uuid.uuid4())
    result = run(first, resent)
    assert ExceptionCategory.CONFIRMED_DUPLICATE in categories(result)


def test_high_value_raises_severity():
    result = run(payment(amount="5000.00", fee="145.30"))
    assert result.exceptions[0].severity is Severity.CRITICAL  # missing_settlement HIGH + high value


def test_totals_never_mix_currencies():
    result = run(payment(), settlement(), bank(),
                 payment("pg_9", currency="EUR"), settlement("STL-9", "pg_9", currency="EUR"),
                 bank("BNK-9", "STL-9", currency="EUR"))
    assert result.summary["matched_totals"]["settlement_to_bank"] == {"EUR": "96.80", "USD": "96.80"}


def test_input_order_does_not_change_result():
    txns = [payment(), settlement(), bank(), bank("BNK-2"), payment("pg_2", amount="10.00"),
            bank("BNK-3", reference="STL-O2", amount="7.00")]
    baseline = reconcile(txns, DEFAULT_CONFIG)
    for seed in range(5):
        shuffled = txns[:]
        random.Random(seed).shuffle(shuffled)
        assert reconcile(shuffled, DEFAULT_CONFIG) == baseline


def replace_leg(leg: LegConfig, **changes) -> LegConfig:
    return leg.model_copy(update=changes)


@pytest.mark.parametrize("leg", DEFAULT_CONFIG.legs, ids=lambda leg: leg.name)
def test_config_snapshot_round_trips(leg):
    assert LegConfig.model_validate(leg.model_dump(mode="json")) == leg


# --- anomalies ---


def gateway_history(n=30, merchant="Shop"):
    rng = random.Random(1)
    return [payment(f"pg_h{i}", amount=f"{rng.randint(50, 150)}.00", fee="1.00", merchant=merchant,
                    timestamp=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=i % 28, hours=i))
            for i in range(n)]


def anomalies(result):
    return [e for e in result.exceptions if e.category is ExceptionCategory.ANOMALY]


def test_amount_outlier_flagged_with_statistics():
    history = gateway_history()
    big = payment("pg_big", amount="25000.00", fee="1.00", timestamp=datetime(2026, 10, 9, tzinfo=UTC))
    [exc] = [e for e in anomalies(reconcile([*history, big], DEFAULT_CONFIG))
             if e.rule_code == "anomaly.amount_outlier"]
    assert exc.transaction_ids == (big.id,)
    assert exc.discrepancy_amount is None
    assert exc.severity is Severity.HIGH  # value at stake above threshold
    assert Decimal(exc.details["robust_z"]) > 6
    assert "not a fraud determination" in exc.details["note"]


def test_amount_outlier_needs_enough_history():
    history = gateway_history(n=10)
    big = payment("pg_big", amount="25000.00", fee="1.00")
    assert not [e for e in anomalies(reconcile([*history, big], DEFAULT_CONFIG))
                if e.rule_code == "anomaly.amount_outlier"]


def test_payment_burst_is_one_exception_per_cluster():
    t0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    burst = [payment(f"pg_b{i}", amount=f"{10 + i}.00", fee="0.50", timestamp=t0 + timedelta(minutes=9 * i))
             for i in range(6)]
    [exc] = anomalies(reconcile(burst, DEFAULT_CONFIG))
    assert exc.rule_code == "anomaly.payment_burst"
    assert len(exc.transaction_ids) == 6
    assert exc.details["payments"] == 6


def test_spread_out_payments_are_not_a_burst():
    t0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    spread = [payment(f"pg_s{i}", amount=f"{10 + i}.00", fee="0.50", timestamp=t0 + timedelta(minutes=20 * i))
              for i in range(6)]
    assert anomalies(reconcile(spread, DEFAULT_CONFIG)) == []

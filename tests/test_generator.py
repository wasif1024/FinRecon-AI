from decimal import Decimal

from finrecon_ai.synthetic.generator import BURST_EXTRA_PAYMENTS, Scenario, add_business_days, expected_fee, generate


def test_same_seed_same_data():
    assert generate(seed=7, payments=200) == generate(seed=7, payments=200)
    assert generate(seed=7, payments=200).bank != generate(seed=8, payments=200).bank


def test_every_scenario_is_planted():
    counts = generate(payments=1000).manifest["scenario_counts"]
    assert all(counts[s.value] > 0 for s in Scenario), counts


def test_record_counts_follow_from_scenarios():
    data = generate(payments=1000)
    c = data.manifest["scenario_counts"]
    extra = c["duplicate_payment"] + BURST_EXTRA_PAYMENTS * c["payment_burst"]
    assert len(data.gateway) == 1000 + extra
    assert len(data.settlements) == 1000 + extra - c["failed_payment"] - c["missing_settlement"]
    expected_bank = (len(data.settlements) - c["missing_bank_credit"] + c["duplicate_bank_credit"]
                     + c["unexplained_bank_credit"] + c["bank_only_charge"] + c["malformed_record"])
    assert len(data.bank) == expected_bank


def test_clean_settlement_net_equals_gross_minus_expected_fee():
    data = generate(payments=300)
    planted = {pid for case in data.manifest["scenarios"] for pid in case["payment_ids"]}
    payments = {p["id"]: p for p in data.gateway}
    for row in data.settlements:
        if row["payment_id"] in planted:
            continue
        gross = Decimal(row["gross_amount"])
        assert Decimal(row["fee_amount"]) == expected_fee(gross)
        assert Decimal(row["net_amount"]) == gross - expected_fee(gross)
        assert Decimal(str(payments[row["payment_id"]]["amount"])) == gross


def test_malformed_rows_are_at_recorded_positions():
    data = generate(payments=300)
    for case in data.manifest["scenarios"]:
        if case["scenario"] == Scenario.MALFORMED_RECORD:
            row = data.bank[case["details"]["row"] - 1]
            assert row["transaction_id"] == case["bank_transaction_ids"][0]


def test_add_business_days_skips_weekends():
    from datetime import date

    friday = date(2026, 10, 2)
    assert add_business_days(friday, 1) == date(2026, 10, 5)
    assert add_business_days(friday, 2) == date(2026, 10, 6)

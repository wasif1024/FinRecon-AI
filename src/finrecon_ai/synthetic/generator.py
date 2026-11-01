"""Deterministic synthetic data for the three MVP sources, with planted reconciliation problems.

Money flow being simulated:
    customer pays -> payment gateway (gross, fee) -> merchant settlement (net = gross - fee)
    -> bank statement credit (net, reference = settlement ID)

The manifest records every planted scenario. It is the ground truth for matching tests (Phase 1c)
and agent evaluations (Phase 6).
"""

import copy
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
FEE_RATE = Decimal("0.029")
FEE_FIXED = Decimal("0.30")
BANK_COUNTERPARTY = "PayGate Payments Ltd"

MERCHANTS = [
    ("Northwind Outfitters", "USD"),
    ("Blue Harbor Coffee", "USD"),
    ("Summit Electronics", "USD"),
    ("Lumen Books", "USD"),
    ("Atelier Rive", "EUR"),
    ("Fjord Design Studio", "EUR"),
    ("Thames Tea Company", "GBP"),
    ("Kensington Prints", "GBP"),
]
FIRST_NAMES = "Ava Liam Noah Emma Omar Sara Yusuf Mia Lucas Zara Hana Ali Leo Iris Kai Nora Ravi Elena".split()
LAST_NAMES = "Khan Smith Garcia Chen Patel Müller Rossi Nguyen Silva Haddad Kowalski Ito Okafor Berg".split()


class Scenario(StrEnum):
    CLEAN = "clean"
    FAILED_PAYMENT = "failed_payment"  # gateway only; correctly never settles
    FEE_DISCREPANCY = "fee_discrepancy"  # settlement fee differs from the gateway fee
    BANK_AMOUNT_MISMATCH = "bank_amount_mismatch"  # bank credit differs from settlement net
    MISSING_SETTLEMENT = "missing_settlement"  # succeeded payment with no settlement or bank credit
    MISSING_BANK_CREDIT = "missing_bank_credit"  # settlement exists, money never arrived
    DUPLICATE_BANK_CREDIT = "duplicate_bank_credit"  # same credit booked twice
    DUPLICATE_PAYMENT = "duplicate_payment"  # customer charged twice minutes apart
    DELAYED_SETTLEMENT = "delayed_settlement"  # bank credit 7-12 days late
    WRONG_BANK_REFERENCE = "wrong_bank_reference"  # reference typo on the bank line
    CURRENCY_MISMATCH = "currency_mismatch"  # bank booked in the wrong currency
    UNEXPLAINED_BANK_CREDIT = "unexplained_bank_credit"  # bank only, no source record
    BANK_ONLY_CHARGE = "bank_only_charge"  # legitimate bank fee, no counterpart expected
    MALFORMED_RECORD = "malformed_record"  # must be rejected at ingestion


# Share of succeeded payments that get each problem. Every scenario appears at least once.
PAYMENT_SCENARIO_RATES = {
    Scenario.FAILED_PAYMENT: 0.02,
    Scenario.FEE_DISCREPANCY: 0.02,
    Scenario.BANK_AMOUNT_MISMATCH: 0.015,
    Scenario.MISSING_SETTLEMENT: 0.01,
    Scenario.MISSING_BANK_CREDIT: 0.01,
    Scenario.DUPLICATE_BANK_CREDIT: 0.01,
    Scenario.DUPLICATE_PAYMENT: 0.01,
    Scenario.DELAYED_SETTLEMENT: 0.015,
    Scenario.WRONG_BANK_REFERENCE: 0.01,
    Scenario.CURRENCY_MISMATCH: 0.005,
}

GATEWAY_FILE = "payment_gateway.json"
SETTLEMENT_FILE = "merchant_settlements.csv"
BANK_FILE = "bank_statement.csv"
MANIFEST_FILE = "manifest.json"

SETTLEMENT_COLUMNS = ["settlement_id", "payment_id", "merchant", "settlement_date", "gross_amount", "fee_amount",
                      "net_amount", "currency"]
BANK_COLUMNS = ["transaction_id", "booking_date", "value_date", "description", "reference", "amount", "currency",
                "counterparty"]


@dataclass
class Case:
    scenario: Scenario
    payment_ids: list[str] = field(default_factory=list)
    settlement_ids: list[str] = field(default_factory=list)
    bank_transaction_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyntheticDataset:
    gateway: list[dict[str, Any]]
    settlements: list[dict[str, str]]
    bank: list[dict[str, str]]
    manifest: dict[str, Any]


def expected_fee(gross: Decimal) -> Decimal:
    return (gross * FEE_RATE + FEE_FIXED).quantize(CENT, ROUND_HALF_UP)


def add_business_days(day: date, days: int) -> date:
    while days > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days -= 1
    return day


def generate(seed: int = 42, payments: int = 1000, start: date = date(2026, 10, 1), days: int = 31) -> SyntheticDataset:
    rng = random.Random(seed)
    scenarios = _assign_scenarios(rng, payments)
    gateway: list[dict[str, Any]] = []
    settlements: list[dict[str, str]] = []
    bank: list[dict[str, str]] = []
    cases: list[Case] = []
    used_bank_ids: set[str] = set()

    def bank_id() -> str:
        while (candidate := f"BNK-{rng.getrandbits(40):010X}") in used_bank_ids:
            pass
        used_bank_ids.add(candidate)
        return candidate

    payment_seq = 0
    settlement_seq = 0

    def new_payment(created: datetime, gross: Decimal, currency: str, merchant: str, customer: str,
                    order_ref: str) -> dict[str, Any]:
        nonlocal payment_seq
        payment_seq += 1
        return {
            "id": f"pg_{payment_seq:06d}",
            "order_reference": order_ref,
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "amount": gross,
            "fee": expected_fee(gross),
            "currency": currency,
            "status": "succeeded",
            "customer_name": customer,
            "merchant_name": merchant,
            "description": f"Order {order_ref}",
        }

    for index, scenario in enumerate(scenarios, start=1):
        merchant, currency = rng.choice(MERCHANTS)
        created = datetime.combine(
            start + timedelta(days=rng.randrange(days)),
            time(rng.randrange(24), rng.randrange(60), rng.randrange(60)),
            tzinfo=UTC,
        )
        gross = Decimal(rng.randint(500, 200_000)) / 100
        if scenario in (Scenario.FEE_DISCREPANCY, Scenario.BANK_AMOUNT_MISMATCH):
            gross += 20  # keep the net amount positive after the planted deduction
        customer = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        payment = new_payment(created, gross, currency, merchant, customer, f"ORD-{index:06d}")
        gateway.append(payment)
        case = Case(scenario, payment_ids=[payment["id"]])
        cases.append(case)

        if scenario is Scenario.FAILED_PAYMENT:
            payment["status"] = "failed"
            continue

        to_settle = [payment]
        if scenario is Scenario.DUPLICATE_PAYMENT:
            duplicate = new_payment(created + timedelta(minutes=rng.randint(1, 5)), gross, currency, merchant,
                                    customer, payment["order_reference"])
            gateway.append(duplicate)
            case.payment_ids.append(duplicate["id"])
            to_settle.append(duplicate)

        if scenario is Scenario.MISSING_SETTLEMENT:
            continue

        for paid in to_settle:
            settlement_seq += 1
            settlement_id = f"STL-{settlement_seq:06d}"
            fee = paid["fee"]
            if scenario is Scenario.FEE_DISCREPANCY:
                extra = Decimal(rng.randint(50, 500)) / 100
                fee += extra
                case.details = {"expected_fee": str(paid["fee"]), "charged_fee": str(fee), "difference": str(extra)}
            net = paid["amount"] - fee
            settled_on = add_business_days(datetime.fromisoformat(paid["created_at"]).date(), 2)
            settlements.append({
                "settlement_id": settlement_id,
                "payment_id": paid["id"],
                "merchant": merchant,
                "settlement_date": settled_on.strftime("%d/%m/%Y"),
                "gross_amount": f"{paid['amount']:.2f}",
                "fee_amount": f"{fee:.2f}",
                "net_amount": f"{net:.2f}",
                "currency": currency,
            })
            case.settlement_ids.append(settlement_id)

            if scenario is Scenario.MISSING_BANK_CREDIT:
                continue

            amount, booked_on, reference, bank_currency = net, add_business_days(settled_on, 1), settlement_id, currency
            if scenario is Scenario.BANK_AMOUNT_MISMATCH:
                delta = Decimal(rng.randint(1, 200)) / 100
                amount = net - delta
                case.details = {"settlement_net": str(net), "bank_amount": str(amount), "difference": str(delta)}
            elif scenario is Scenario.DELAYED_SETTLEMENT:
                delay = rng.randint(7, 12)
                booked_on += timedelta(days=delay)
                case.details = {"expected_booking_date": add_business_days(settled_on, 1).isoformat(),
                                "delay_days": delay}
            elif scenario is Scenario.WRONG_BANK_REFERENCE:
                # Letter O instead of zero: looks right to a human, never collides with a real ID.
                reference = settlement_id.replace("0", "O", 1)
                case.details = {"correct_reference": settlement_id, "bank_reference": reference}
            elif scenario is Scenario.CURRENCY_MISMATCH:
                bank_currency = "EUR" if currency == "USD" else "USD"
                case.details = {"expected_currency": currency, "bank_currency": bank_currency}

            copies = 2 if scenario is Scenario.DUPLICATE_BANK_CREDIT else 1
            for _ in range(copies):
                row = _bank_row(bank_id(), booked_on, f"PAYGATE SETTLEMENT {reference}", reference, amount,
                                bank_currency, BANK_COUNTERPARTY)
                bank.append(row)
                case.bank_transaction_ids.append(row["transaction_id"])

    cases.extend(_bank_only_rows(rng, bank, bank_id, start, days))
    bank.sort(key=lambda r: (r["booking_date"], r["transaction_id"]))
    settlements.sort(key=lambda r: (datetime.strptime(r["settlement_date"], "%d/%m/%Y"), r["settlement_id"]))
    cases.extend(_insert_malformed(rng, bank, bank_id))

    for payment in gateway:  # JSON numbers, not strings; ingestion must parse them as exact decimals
        payment["amount"] = float(payment["amount"])
        payment["fee"] = float(payment["fee"])

    manifest = {
        "seed": seed,
        "payments_requested": payments,
        "period": {"start": start.isoformat(), "days": days},
        "files": {
            "payment_gateway": {"file": GATEWAY_FILE, "records": len(gateway)},
            "merchant_settlement": {"file": SETTLEMENT_FILE, "records": len(settlements)},
            "bank_statement": {"file": BANK_FILE, "records": len(bank)},
        },
        "scenario_counts": _counts(cases),
        "scenarios": [_case_to_dict(c) for c in cases if c.scenario is not Scenario.CLEAN],
    }
    return SyntheticDataset(gateway, settlements, bank, manifest)


def write_dataset(dataset: SyntheticDataset, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / GATEWAY_FILE).write_text(json.dumps({"object": "list", "data": dataset.gateway}, indent=2,
                                                   ensure_ascii=False))
    _write_csv(out_dir / SETTLEMENT_FILE, SETTLEMENT_COLUMNS, dataset.settlements)
    _write_csv(out_dir / BANK_FILE, BANK_COLUMNS, dataset.bank)
    (out_dir / MANIFEST_FILE).write_text(json.dumps(dataset.manifest, indent=2))


def _assign_scenarios(rng: random.Random, payments: int) -> list[Scenario]:
    planted = [s for s, rate in PAYMENT_SCENARIO_RATES.items() for _ in range(max(1, round(rate * payments)))]
    if len(planted) > payments:
        raise ValueError(f"need at least {len(planted)} payments to plant every scenario")
    scenarios = planted + [Scenario.CLEAN] * (payments - len(planted))
    rng.shuffle(scenarios)
    return scenarios


def _bank_row(txn_id: str, booked_on: date, description: str, reference: str, amount: Decimal, currency: str,
              counterparty: str) -> dict[str, str]:
    return {
        "transaction_id": txn_id,
        "booking_date": booked_on.isoformat(),
        "value_date": booked_on.isoformat(),
        "description": description,
        "reference": reference,
        "amount": f"{amount:.2f}",
        "currency": currency,
        "counterparty": counterparty,
    }


def _bank_only_rows(rng: random.Random, bank: list[dict[str, str]], bank_id, start: date, days: int) -> list[Case]:
    cases = []
    month_end = start + timedelta(days=days - 1)
    for currency in ("USD", "EUR", "GBP"):
        row = _bank_row(bank_id(), month_end, "ACCOUNT MAINTENANCE FEE", "", Decimal("-25.00"), currency,
                        "Bank charges")
        bank.append(row)
        cases.append(Case(Scenario.BANK_ONLY_CHARGE, bank_transaction_ids=[row["transaction_id"]]))
    for _ in range(2):
        amount = Decimal(rng.randint(10_000, 500_000)) / 100
        row = _bank_row(bank_id(), start + timedelta(days=rng.randrange(days)), "INCOMING TRANSFER", "",
                        amount, "USD", "Unknown remitter")
        bank.append(row)
        cases.append(Case(Scenario.UNEXPLAINED_BANK_CREDIT, bank_transaction_ids=[row["transaction_id"]]))
    return cases


def _insert_malformed(rng: random.Random, bank: list[dict[str, str]], bank_id) -> list[Case]:
    defects = [
        ("amount", "12.3.4", "amount is not a number"),
        ("booking_date", "2026-13-45", "booking date does not exist"),
        ("currency", "", "currency is missing"),
    ]
    inserted = []
    for column, bad_value, reason in defects:
        row = copy.deepcopy(rng.choice(bank))
        row["transaction_id"] = bank_id()
        row[column] = bad_value
        bank.insert(rng.randrange(len(bank) + 1), row)
        inserted.append((row, column, reason))
    cases = []
    for row, column, reason in inserted:
        cases.append(Case(
            Scenario.MALFORMED_RECORD,
            bank_transaction_ids=[row["transaction_id"]],
            details={"file": BANK_FILE, "row": bank.index(row) + 1, "column": column, "reason": reason},
        ))
    return cases


def _counts(cases: list[Case]) -> dict[str, int]:
    counts = {s.value: 0 for s in Scenario}
    for case in cases:
        counts[case.scenario.value] += 1
    return counts


def _case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "scenario": case.scenario.value,
        "payment_ids": case.payment_ids,
        "settlement_ids": case.settlement_ids,
        "bank_transaction_ids": case.bank_transaction_ids,
        "details": case.details,
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

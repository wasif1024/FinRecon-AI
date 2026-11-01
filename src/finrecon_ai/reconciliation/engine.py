"""Deterministic matching engine (FR-03, FR-04). Pure functions: no database, no clock, no randomness.

Each leg matches a left source to the right source it flows into:
  1. Exact reference pass: left key == right key, then compare currency, amount, fees, date, merchant.
     Extra right records claiming the same left record are duplicates.
  2. Fallback pass: unmatched records with identical amount + currency inside the date window.
     A single candidate is a partial match (reference disagrees); several candidates are left unmatched.
  3. Whatever remains is unmatched on its side.
Then each source is scanned for duplicates within itself, and for anomalies (amount outliers, payment bursts).
"""

import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from finrecon_ai.db.enums import (
    Direction,
    ExceptionCategory,
    MatchClassification,
    MatchStrategy,
    Severity,
    SourceType,
    TransactionStatus,
)
from finrecon_ai.reconciliation.config import AnomalyConfig, LegConfig, ReconciliationConfig


@dataclass(frozen=True)
class Txn:
    id: uuid.UUID
    source_id: uuid.UUID
    source_type: SourceType
    source_transaction_id: str | None
    external_reference: str | None
    transaction_date: date
    transaction_timestamp: datetime | None
    amount: Decimal
    fee_amount: Decimal | None
    currency: str
    direction: Direction
    counterparty: str | None
    merchant: str | None
    status: TransactionStatus


@dataclass(frozen=True)
class Match:
    leg: str
    classification: MatchClassification
    strategy: MatchStrategy
    score: Decimal
    transaction_ids: tuple[uuid.UUID, ...]  # left record first
    amount_difference: Decimal | None
    currency: str | None
    explanation: dict[str, Any]


@dataclass(frozen=True)
class ExceptionRecord:
    category: ExceptionCategory
    severity: Severity
    transaction_ids: tuple[uuid.UUID, ...]
    discrepancy_amount: Decimal | None
    currency: str | None
    rule_code: str
    details: dict[str, Any]
    match_index: int | None = None  # index into ReconciliationResult.matches


@dataclass
class ReconciliationResult:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[ExceptionRecord] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
BASE_SEVERITY = {
    ExceptionCategory.CURRENCY_MISMATCH: Severity.HIGH,
    ExceptionCategory.CONFIRMED_DUPLICATE: Severity.HIGH,
    ExceptionCategory.MISSING_SETTLEMENT: Severity.HIGH,
    ExceptionCategory.UNMATCHED: Severity.MEDIUM,
    ExceptionCategory.AMOUNT_MISMATCH: Severity.MEDIUM,
    ExceptionCategory.FEE_DISCREPANCY: Severity.MEDIUM,
    ExceptionCategory.POTENTIAL_DUPLICATE: Severity.MEDIUM,
    ExceptionCategory.DATE_MISMATCH: Severity.LOW,
    ExceptionCategory.PARTIALLY_MATCHED: Severity.LOW,
    ExceptionCategory.ANOMALY: Severity.MEDIUM,
}
# Scales MAD to be comparable with a standard deviation for normal data.
MAD_SCALE = Decimal("0.6745")
ANOMALY_NOTE = "statistical indicator for review; not a fraud determination"
CLASSIFICATION_TO_CATEGORY = {
    MatchClassification.CURRENCY_MISMATCH: ExceptionCategory.CURRENCY_MISMATCH,
    MatchClassification.AMOUNT_MISMATCH: ExceptionCategory.AMOUNT_MISMATCH,
    MatchClassification.DATE_MISMATCH: ExceptionCategory.DATE_MISMATCH,
    MatchClassification.PARTIALLY_MATCHED: ExceptionCategory.PARTIALLY_MATCHED,
    MatchClassification.DUPLICATE: ExceptionCategory.CONFIRMED_DUPLICATE,
}


def reconcile(transactions: Iterable[Txn], config: ReconciliationConfig) -> ReconciliationResult:
    txns = sorted(transactions, key=_sort_key)
    by_source: dict[SourceType, list[Txn]] = defaultdict(list)
    for txn in txns:
        by_source[txn.source_type].append(txn)

    result = ReconciliationResult()
    duplicate_ids: set[uuid.UUID] = set()
    for leg in config.legs:
        _reconcile_leg(leg, by_source[leg.left_source], by_source[leg.right_source], config, result, duplicate_ids)
    _detect_duplicates(txns, config, result, duplicate_ids)
    _detect_anomalies(txns, config.anomalies, config, result)
    result.summary = _summarize(txns, result)
    return result


# --- legs ---


@dataclass
class _Comparison:
    classification: MatchClassification
    strategy: MatchStrategy
    rules: list[dict[str, Any]]
    amount_difference: Decimal | None
    fee_difference: Decimal | None

    @property
    def score(self) -> Decimal:
        passed = sum(1 for r in self.rules if r["passed"])
        return (Decimal(passed) / Decimal(len(self.rules))).quantize(Decimal("0.0001"))


def _reconcile_leg(
    leg: LegConfig,
    left_txns: Sequence[Txn],
    right_txns: Sequence[Txn],
    config: ReconciliationConfig,
    result: ReconciliationResult,
    duplicate_ids: set[uuid.UUID],
) -> None:
    lefts = [t for t in left_txns if t.status in leg.left_statuses]
    right_by_key: dict[str, list[Txn]] = defaultdict(list)
    for right in right_txns:
        if key := _norm_key(getattr(right, leg.right_key)):
            right_by_key[key].append(right)

    matched: set[uuid.UUID] = set()

    # Pass 1: exact reference.
    for left in lefts:
        key = _norm_key(getattr(left, leg.left_key))
        candidates = [r for r in right_by_key.get(key, []) if r.id not in matched] if key else []
        if not candidates:
            continue
        compared = sorted(((_compare(leg, left, r, reference_matched=True), r) for r in candidates),
                          key=lambda cr: (-cr[0].score, _sort_key(cr[1])))
        (comparison, primary), extras = compared[0], compared[1:]
        _add_match(leg, left, primary, comparison, config, result)
        matched.update({left.id, primary.id})

        for _, extra in extras:
            index = len(result.matches)
            result.matches.append(Match(
                leg=leg.name,
                classification=MatchClassification.DUPLICATE,
                strategy=MatchStrategy.EXACT_REFERENCE,
                score=Decimal("1"),
                transaction_ids=(left.id, extra.id),
                amount_difference=None,
                currency=extra.currency,
                explanation={"leg": leg.name, "summary": f"{extra.source_type} record {extra.source_transaction_id} "
                             f"repeats reference {key} already matched to {primary.source_transaction_id}",
                             "duplicate_of": str(primary.id)},
            ))
            result.exceptions.append(_exception(
                ExceptionCategory.CONFIRMED_DUPLICATE, (extra.id, primary.id, left.id), extra.amount, extra.currency,
                rule_code=f"{leg.name}.duplicate_reference", config=config, match_index=index,
                details={"leg": leg.name, "reference": key, "duplicate_transaction_id": str(extra.id)},
            ))
            matched.add(extra.id)
            duplicate_ids.add(extra.id)

    # Pass 2: amount + currency + date window for whatever did not join on reference.
    # Exact amounts only; tolerance applies to reference matches.
    ambiguous: dict[uuid.UUID, list[str]] = {}
    if leg.fallback_matching:
        pool: dict[tuple[str, Decimal], list[Txn]] = defaultdict(list)
        allowed = leg.fallback_right_counterparties
        for right in right_txns:
            if right.id not in matched and (not allowed or _norm_text(right.counterparty) in allowed):
                pool[(right.currency, _amount(right, leg.right_amount))].append(right)
        for left in lefts:
            if left.id in matched:
                continue
            candidates = [r for r in pool.get((left.currency, _amount(left, leg.left_amount)), [])
                          if r.id not in matched and _lag_ok(leg, left, r)]
            if len(candidates) == 1:
                right = candidates[0]
                _add_match(leg, left, right, _compare(leg, left, right, reference_matched=False), config, result)
                matched.update({left.id, right.id})
            elif candidates:
                ambiguous[left.id] = [str(r.id) for r in candidates]

    # Pass 3: unmatched on each side.
    for side, txns, category, other in (
        ("left", lefts, leg.left_unmatched_category, leg.right_source),
        ("right", right_txns, leg.right_unmatched_category, leg.left_source),
    ):
        for txn in txns:
            if txn.id in matched:
                continue
            details: dict[str, Any] = {"leg": leg.name, "side": side, "source_type": txn.source_type,
                                       "source_transaction_id": txn.source_transaction_id,
                                       "external_reference": txn.external_reference, "missing_in": other}
            if txn.id in ambiguous:
                details["ambiguous_candidates"] = ambiguous[txn.id]
            result.exceptions.append(_exception(
                category, (txn.id,), txn.amount, txn.currency, rule_code=f"{leg.name}.unmatched_{side}",
                config=config, details=details,
            ))


def _compare(leg: LegConfig, left: Txn, right: Txn, *, reference_matched: bool) -> _Comparison:
    rules: list[dict[str, Any]] = [{
        "rule": "reference",
        "left": getattr(left, leg.left_key),
        "right": getattr(right, leg.right_key),
        "passed": reference_matched,
    }]

    currency_ok = left.currency == right.currency
    rules.append({"rule": "currency", "left": left.currency, "right": right.currency, "passed": currency_ok})

    amount_difference = fee_difference = None
    amount_ok = False
    if currency_ok:
        left_amount, right_amount = _amount(left, leg.left_amount), _amount(right, leg.right_amount)
        amount_difference = right_amount - left_amount
        amount_ok = abs(amount_difference) <= leg.amount_tolerance
        rules.append({"rule": "amount", "basis": [leg.left_amount, leg.right_amount], "left": str(left_amount),
                      "right": str(right_amount), "difference": str(amount_difference),
                      "tolerance": str(leg.amount_tolerance), "passed": amount_ok})
        if leg.compare_fees and not amount_ok and left.fee_amount is not None and right.fee_amount is not None:
            fee_difference = right.fee_amount - left.fee_amount
            rules.append({"rule": "fee", "left": str(left.fee_amount), "right": str(right.fee_amount),
                          "difference": str(fee_difference), "explains_amount_difference":
                          fee_difference == -amount_difference, "passed": fee_difference == 0})
    else:
        rules.append({"rule": "amount", "passed": False, "note": "not compared across currencies"})

    lag = (right.transaction_date - left.transaction_date).days
    date_ok = leg.min_lag_days <= lag <= leg.max_lag_days
    rules.append({"rule": "date_window", "left": left.transaction_date.isoformat(),
                  "right": right.transaction_date.isoformat(), "lag_days": lag,
                  "window": [leg.min_lag_days, leg.max_lag_days], "passed": date_ok})

    merchant_ok = True
    if leg.compare_merchant:
        merchant_ok = _norm_text(left.merchant) == _norm_text(right.merchant)
        rules.append({"rule": "merchant", "left": left.merchant, "right": right.merchant, "passed": merchant_ok})

    if not currency_ok:
        classification = MatchClassification.CURRENCY_MISMATCH
    elif not amount_ok:
        classification = MatchClassification.AMOUNT_MISMATCH
    elif not date_ok:
        classification = MatchClassification.DATE_MISMATCH
    elif not reference_matched or not merchant_ok:
        classification = MatchClassification.PARTIALLY_MATCHED
    else:
        classification = MatchClassification.MATCHED

    if not reference_matched:
        strategy = MatchStrategy.AMOUNT_CURRENCY
    elif amount_ok and amount_difference:
        strategy = MatchStrategy.TOLERANCE
    else:
        strategy = MatchStrategy.EXACT_REFERENCE
    return _Comparison(classification, strategy, rules, amount_difference, fee_difference)


def _add_match(leg: LegConfig, left: Txn, right: Txn, comparison: _Comparison, config: ReconciliationConfig,
               result: ReconciliationResult) -> None:
    failed = [r["rule"] for r in comparison.rules if not r["passed"]]
    summary = "all rules passed" if not failed else "failed: " + ", ".join(failed)
    index = len(result.matches)
    result.matches.append(Match(
        leg=leg.name,
        classification=comparison.classification,
        strategy=comparison.strategy,
        score=comparison.score,
        transaction_ids=(left.id, right.id),
        amount_difference=comparison.amount_difference,
        currency=left.currency if left.currency == right.currency else None,
        explanation={"leg": leg.name, "summary": summary, "rules": comparison.rules},
    ))
    if comparison.classification is MatchClassification.MATCHED:
        return

    category = CLASSIFICATION_TO_CATEGORY[comparison.classification]
    if category is ExceptionCategory.AMOUNT_MISMATCH and comparison.fee_difference:
        category = ExceptionCategory.FEE_DISCREPANCY
    if category is ExceptionCategory.CURRENCY_MISMATCH:
        amount_at_issue, currency = right.amount, right.currency
    elif comparison.amount_difference:
        amount_at_issue, currency = abs(comparison.amount_difference), left.currency
    else:
        amount_at_issue, currency = None, left.currency
    result.exceptions.append(_exception(
        category, (left.id, right.id), amount_at_issue, currency, rule_code=f"{leg.name}.{comparison.classification}",
        config=config, match_index=index, details={"leg": leg.name, "summary": summary, "failed_rules": failed},
    ))


# --- duplicates within a source ---


def _detect_duplicates(txns: Sequence[Txn], config: ReconciliationConfig, result: ReconciliationResult,
                       already_flagged: set[uuid.UUID]) -> None:
    groups: dict[tuple[Any, ...], list[Txn]] = defaultdict(list)
    for txn in txns:
        reference, counterparty = _norm_key(txn.external_reference), _norm_text(txn.counterparty)
        if not reference and not counterparty:
            continue  # too little identity to call anything a duplicate
        groups[(txn.source_id, txn.currency, txn.amount, txn.direction, counterparty, reference)].append(txn)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=_moment)
        cluster = [members[0]]
        for txn in [*members[1:], None]:
            if txn is not None and _within_window(cluster[-1], txn, config.duplicate_window_minutes):
                cluster.append(txn)
                continue
            if len(cluster) > 1 and not any(t.id in already_flagged for t in cluster):
                same_record = len({t.source_transaction_id for t in cluster}) == 1
                category = (ExceptionCategory.CONFIRMED_DUPLICATE if same_record
                            else ExceptionCategory.POTENTIAL_DUPLICATE)
                first = cluster[0]
                result.exceptions.append(_exception(
                    category, tuple(t.id for t in cluster), first.amount * (len(cluster) - 1), first.currency,
                    rule_code="duplicate.same_source_record" if same_record else "duplicate.similar_within_window",
                    config=config,
                    details={"source_type": first.source_type,
                             "source_transaction_ids": [t.source_transaction_id for t in cluster],
                             "window_minutes": config.duplicate_window_minutes,
                             "matched_on": ["amount", "currency", "direction", "counterparty", "reference"]},
                ))
            if txn is not None:
                cluster = [txn]


# --- anomalies (FR-08) ---


def _detect_anomalies(txns: Sequence[Txn], anomalies: AnomalyConfig, config: ReconciliationConfig,
                      result: ReconciliationResult) -> None:
    scoped = [t for t in txns if t.source_type in anomalies.source_types]

    by_merchant: dict[tuple[Any, ...], list[Txn]] = defaultdict(list)
    for txn in scoped:
        by_merchant[(txn.source_id, _norm_text(txn.merchant), txn.currency)].append(txn)
    for (_, merchant, currency), group in by_merchant.items():
        if len(group) < anomalies.amount_min_samples:
            continue
        amounts = [t.amount for t in group]
        median = statistics.median(amounts)
        mad = statistics.median(abs(a - median) for a in amounts)
        if mad == 0:
            continue
        for txn in group:
            z = MAD_SCALE * (txn.amount - median) / mad
            if abs(z) >= anomalies.amount_outlier_z:
                result.exceptions.append(_exception(
                    ExceptionCategory.ANOMALY, (txn.id,), None, txn.currency, rule_code="anomaly.amount_outlier",
                    config=config, severity_amount=txn.amount,
                    details={"indicator": "amount far outside the merchant's usual range", "note": ANOMALY_NOTE,
                             "merchant": txn.merchant, "amount": str(txn.amount), "median": str(median),
                             "mad": str(mad), "robust_z": str(z.quantize(Decimal("0.01"))),
                             "threshold": str(anomalies.amount_outlier_z), "sample_size": len(group)},
                ))

    by_payer: dict[tuple[Any, ...], list[Txn]] = defaultdict(list)
    for txn in scoped:
        if txn.transaction_timestamp and txn.counterparty:
            by_payer[(txn.source_id, _norm_text(txn.counterparty), _norm_text(txn.merchant))].append(txn)
    window = anomalies.velocity_window_minutes * 60
    for group in by_payer.values():
        if len(group) < anomalies.velocity_count:
            continue
        group.sort(key=_moment)
        flagged = [False] * len(group)
        end = 0
        for start in range(len(group)):
            end = max(end, start)
            while (end + 1 < len(group) and (group[end + 1].transaction_timestamp
                                             - group[start].transaction_timestamp).total_seconds() <= window):
                end += 1
            if end - start + 1 >= anomalies.velocity_count:
                for i in range(start, end + 1):
                    flagged[i] = True
        cluster: list[Txn] = []
        for txn, is_flagged in [*zip(group, flagged, strict=True), (None, False)]:
            if is_flagged:
                cluster.append(txn)
                continue
            if cluster:
                first = cluster[0]
                currencies = {t.currency for t in cluster}
                total = sum((t.amount for t in cluster), Decimal("0")) if len(currencies) == 1 else None
                result.exceptions.append(_exception(
                    ExceptionCategory.ANOMALY, tuple(t.id for t in cluster), None,
                    first.currency if total is not None else None, rule_code="anomaly.payment_burst",
                    config=config, severity_amount=total,
                    details={"indicator": "unusually many payments from one counterparty in a short window",
                             "note": ANOMALY_NOTE, "counterparty": first.counterparty, "merchant": first.merchant,
                             "payments": len(cluster), "total": str(total) if total is not None else None,
                             "first": first.transaction_timestamp.isoformat(),
                             "last": cluster[-1].transaction_timestamp.isoformat(),
                             "window_minutes": anomalies.velocity_window_minutes,
                             "source_transaction_ids": [t.source_transaction_id for t in cluster]},
                ))
                cluster = []


def _moment(txn: Txn) -> tuple[date, datetime | None, str]:
    return (txn.transaction_date, txn.transaction_timestamp, txn.source_transaction_id or "")


def _within_window(a: Txn, b: Txn, minutes: int) -> bool:
    if a.transaction_timestamp and b.transaction_timestamp:
        return abs((b.transaction_timestamp - a.transaction_timestamp).total_seconds()) <= minutes * 60
    return a.transaction_date == b.transaction_date


# --- helpers ---


def _exception(category: ExceptionCategory, transaction_ids: tuple[uuid.UUID, ...], amount: Decimal | None,
               currency: str | None, *, rule_code: str, config: ReconciliationConfig, details: dict[str, Any],
               match_index: int | None = None, severity_amount: Decimal | None = None) -> ExceptionRecord:
    """amount is the discrepancy; severity_amount (default: amount) is the value at stake for severity."""
    severity = BASE_SEVERITY[category]
    at_stake = severity_amount if severity_amount is not None else amount
    # Threshold is applied in the record's own currency; no cross-currency conversion (SRS §10).
    if at_stake is not None and at_stake >= config.high_value_threshold:
        severity = SEVERITY_ORDER[min(SEVERITY_ORDER.index(severity) + 1, len(SEVERITY_ORDER) - 1)]
    return ExceptionRecord(category, severity, transaction_ids, amount, currency, rule_code, details, match_index)


def _amount(txn: Txn, basis: str) -> Decimal:
    if basis == "net_of_fee":
        return txn.amount - (txn.fee_amount or Decimal("0"))
    return txn.amount


def _lag_ok(leg: LegConfig, left: Txn, right: Txn) -> bool:
    return leg.min_lag_days <= (right.transaction_date - left.transaction_date).days <= leg.max_lag_days


def _norm_key(value: str | None) -> str:
    return (value or "").strip().upper()


def _norm_text(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _sort_key(txn: Txn) -> tuple[str, date, str, str]:
    return (txn.source_type, txn.transaction_date, txn.source_transaction_id or "", str(txn.id))


def _summarize(txns: Sequence[Txn], result: ReconciliationResult) -> dict[str, Any]:
    by_id = {t.id: t for t in txns}
    matched_totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for match in result.matches:
        if match.classification is MatchClassification.MATCHED:
            left = by_id[match.transaction_ids[0]]
            matched_totals[match.leg][left.currency] += left.amount
    discrepancy_totals: dict[str, Decimal] = defaultdict(Decimal)
    for exc in result.exceptions:
        if exc.discrepancy_amount is not None and exc.currency:
            discrepancy_totals[exc.currency] += exc.discrepancy_amount

    leg_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for match in result.matches:
        leg_counts[match.leg][match.classification.value] += 1
    return {
        "transactions": dict(sorted(Counter(t.source_type.value for t in txns).items())),
        "matches": {leg: dict(sorted(c.items())) for leg, c in leg_counts.items()},
        "exceptions": dict(sorted(Counter(e.category.value for e in result.exceptions).items())),
        # Totals are per currency; amounts in different currencies are never added together.
        "matched_totals": {leg: {cur: str(v) for cur, v in sorted(t.items())} for leg, t in matched_totals.items()},
        "discrepancy_totals": {cur: str(v) for cur, v in sorted(discrepancy_totals.items())},
    }

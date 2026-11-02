"""Ground truth for agent evaluation: what a good investigation of each planted scenario looks like.

The synthetic generator plants every problem on purpose and records it in its manifest, so each scenario has a known
root cause. These expectations encode what reviewers would accept, deliberately allowing more than one reasonable
resolution where the evidence alone cannot settle the case.
"""

from dataclasses import dataclass

from finrecon_ai.db.enums import ExceptionCategory, ResolutionType
from finrecon_ai.synthetic.generator import Scenario

FEE_POLICY = "Payment Processing Fee Policy"
SETTLEMENT_POLICY = "Settlement Operations Policy"
DUPLICATES_POLICY = "Duplicate Transactions and Reference Errors Policy"

R = ResolutionType


@dataclass(frozen=True)
class Expectation:
    category: ExceptionCategory
    acceptable: tuple[ResolutionType, ...]
    policy: str | None  # the document that governs this kind of case
    root_cause: str  # what actually happened, as planted (details are appended from the manifest)


EXPECTATIONS: dict[Scenario, Expectation] = {
    Scenario.FEE_DISCREPANCY: Expectation(
        ExceptionCategory.FEE_DISCREPANCY, (R.REQUEST_DOCUMENTATION, R.CONFIRM_FEE_DISCREPANCY, R.ESCALATE), FEE_POLICY,
        "The settlement deducted a higher processing fee than the gateway recorded at payment time; the bank credit "
        "matches the settlement."),
    Scenario.BANK_AMOUNT_MISMATCH: Expectation(
        ExceptionCategory.AMOUNT_MISMATCH, (R.REQUEST_DOCUMENTATION, R.NO_ACTION_REQUIRED, R.ESCALATE),
        SETTLEMENT_POLICY,
        "The bank credited slightly less than the settlement net amount, typically a charge deducted in transit."),
    Scenario.MISSING_SETTLEMENT: Expectation(
        ExceptionCategory.MISSING_SETTLEMENT, (R.REQUEST_DOCUMENTATION, R.ESCALATE), SETTLEMENT_POLICY,
        "A successful gateway payment has no settlement and no bank credit at all."),
    Scenario.MISSING_BANK_CREDIT: Expectation(
        ExceptionCategory.UNMATCHED, (R.REQUEST_DOCUMENTATION, R.ESCALATE), SETTLEMENT_POLICY,
        "A settlement exists but the money never arrived in the bank account."),
    Scenario.DUPLICATE_BANK_CREDIT: Expectation(
        ExceptionCategory.CONFIRMED_DUPLICATE, (R.INVESTIGATE_DUPLICATE, R.REQUEST_DOCUMENTATION, R.ESCALATE),
        DUPLICATES_POLICY,
        "The bank booked the same settlement credit twice."),
    Scenario.DUPLICATE_PAYMENT: Expectation(
        ExceptionCategory.POTENTIAL_DUPLICATE, (R.INVESTIGATE_DUPLICATE, R.REQUEST_DOCUMENTATION), DUPLICATES_POLICY,
        "The same customer was charged twice for the same order a few minutes apart; both charges settled."),
    Scenario.DELAYED_SETTLEMENT: Expectation(
        ExceptionCategory.DATE_MISMATCH, (R.NO_ACTION_REQUIRED, R.REQUEST_DOCUMENTATION, R.ESCALATE),
        SETTLEMENT_POLICY,
        "The bank credit arrived in full but a week or more later than expected."),
    Scenario.WRONG_BANK_REFERENCE: Expectation(
        ExceptionCategory.PARTIALLY_MATCHED, (R.MANUAL_MATCH, R.REQUEST_DOCUMENTATION), DUPLICATES_POLICY,
        "The bank credit carries a mistyped settlement reference (a letter O instead of a zero); amount, currency "
        "and date all match the settlement."),
    Scenario.CURRENCY_MISMATCH: Expectation(
        ExceptionCategory.CURRENCY_MISMATCH, (R.REQUEST_DOCUMENTATION, R.ESCALATE), SETTLEMENT_POLICY,
        "The bank credited the settlement in the wrong currency."),
    Scenario.UNUSUAL_AMOUNT: Expectation(
        ExceptionCategory.ANOMALY, (R.ESCALATE, R.REQUEST_DOCUMENTATION, R.NO_ACTION_REQUIRED), DUPLICATES_POLICY,
        "A single payment far above the merchant's usual range; otherwise it reconciles cleanly. An indicator for "
        "review, not proof of wrongdoing."),
    Scenario.PAYMENT_BURST: Expectation(
        ExceptionCategory.ANOMALY, (R.ESCALATE, R.REQUEST_DOCUMENTATION, R.INVESTIGATE_DUPLICATE), DUPLICATES_POLICY,
        "One customer paid the same merchant six times within an hour with different amounts and orders; all "
        "payments reconcile. An indicator for review."),
    Scenario.UNEXPLAINED_BANK_CREDIT: Expectation(
        ExceptionCategory.UNMATCHED, (R.REQUEST_DOCUMENTATION, R.ESCALATE), None,
        "A bank credit from an unknown remitter with no matching gateway payment or settlement."),
    Scenario.BANK_ONLY_CHARGE: Expectation(
        ExceptionCategory.UNMATCHED, (R.NO_ACTION_REQUIRED, R.REQUEST_DOCUMENTATION), None,
        "A routine monthly account maintenance fee debited by the bank; no counterpart is expected."),
}

---
name: fee-discrepancy
description: "Investigate a fee discrepancy: the settlement deducted a different processing fee than the gateway recorded. Use when the case category is fee_discrepancy."
---
# Fee discrepancy

The settlement's fee differs from the fee the gateway recorded when the payment was authorised. The bank credit usually matches the settlement, so the money question is the fee itself.

## Check
1. `get_case_transactions` and `get_match_explanation`: the gateway fee, the settlement fee and the engine's difference. Use the engine's figure.
2. Whether the bank credit equals the settlement net amount (it usually does; if not, there is a second problem to report).
3. `find_similar_cases` and the merchant's other open fee cases this month: repeated discrepancies for one merchant are handled as a pattern.
4. `search_policies` for the fee policy: the expected fee, the thresholds by size of difference, the repeated-discrepancy rule, and what a dispute must include. Cite the passages you apply.

## Usual explanations
A fee schedule change, a surcharge (cross-border, premium card) that the gateway did not show at authorisation, or an error at the gateway. Without an itemised fee breakdown you cannot tell which: say so.

## Usual outcomes
Small differences recorded as explained variance; larger ones need the itemised breakdown before resolution; repeated or large differences are escalated. The policy sets the lines: apply it, do not guess them.

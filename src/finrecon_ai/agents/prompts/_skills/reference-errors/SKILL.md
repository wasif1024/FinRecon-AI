---
name: reference-errors
description: "Investigate a near-miss reference: records that agree on amount, currency and date but whose references do not match exactly. Use when the case category is partially_matched."
---
# Reference errors

The engine found a likely counterpart but the reference does not match, often a keying error at the bank (a letter O for a zero, a dropped or swapped character).

## Check
1. The two references side by side and exactly how they differ.
2. That amount, currency and date window all agree, and that exactly one candidate exists (`search_transactions` around the amount and date). Two candidates rule out a manual match.
3. `search_policies` for reference errors: when a manual match is allowed and who must review it. Cite it.

## Usual outcomes
A manual match when the policy's conditions hold (it still needs a second reviewer); otherwise request documentation.

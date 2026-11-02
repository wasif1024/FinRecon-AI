---
name: missing-records
description: "Investigate money that is missing or unexplained: a payment with no settlement, a settlement with no bank credit, or a bank entry with no counterpart. Use when the case category is missing_settlement or unmatched."
---
# Missing or unexplained records

One record in the chain has no counterpart. Work out which link is missing before explaining it.

## Check
1. Which records exist (`get_case_transactions`) and which leg never matched (`get_match_explanation`; "never paired" means no match was attempted successfully).
2. Search for the counterpart before concluding it is missing (`search_transactions`, or delegate to transaction-tracer): the same amount under a different or mistyped reference, a date a few days later, the amount less a small charge, a different currency.
3. For a bank entry with no counterpart: is it a routine bank-side item (a monthly account fee, interest) that needs no counterpart, or money from an unknown remitter?
4. `search_policies` for missing settlements and missing credits: how long to wait, whom to ask, when to escalate by amount. Cite it.

## Usual outcomes
Request documentation from the gateway or bank, escalate when the amount or the wait exceeds the policy, or record a routine bank-side item as needing no action. Never assume money is lost or stolen; say what is missing and what would confirm it.

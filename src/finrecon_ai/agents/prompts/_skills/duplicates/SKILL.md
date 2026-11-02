---
name: duplicates
description: "Investigate a duplicate: the same settlement credited twice, or the same customer charged twice for one order. Use when the case category is confirmed_duplicate or potential_duplicate."
---
# Duplicates

Two records look like the same money counted twice.

## Check
1. The two records: same reference, amount, counterparty, how far apart in time.
2. Whether they are genuinely the same (one order, one settlement) or two real transactions that happen to look alike (two purchases, a retried card). Order references and timing usually decide it.
3. Whether both settled or were credited, so that money actually moved twice.
4. `search_policies` for duplicates: what may not be reversed before confirmation, who confirms, how a refund is made. Cite it.

## Usual outcomes
Confirm with the bank, merchant or gateway before anything is reversed or refunded; record a suspected duplicate as pending confirmation.

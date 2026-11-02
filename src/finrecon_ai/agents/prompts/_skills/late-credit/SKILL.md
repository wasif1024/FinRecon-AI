---
name: late-credit
description: "Investigate a bank credit that arrived later than expected. Use when the case category is date_mismatch."
---
# Late bank credit

The amounts agree but the credit arrived outside the expected window.

## Check
1. The engine's date lag in business days (`get_match_explanation`) and that the amount and currency match in full.
2. Whether other settlements from the same period were also late (a payout delay at the gateway) or only this one.
3. `search_policies` for settlement timing and late settlements: the expected timing and what each length of delay requires. Cite it.

## Usual outcomes
A credit that arrived in full within the monitored delay needs no action; longer delays need a written inquiry or escalation, as the policy sets out.

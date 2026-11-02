---
name: anomaly-indicators
description: "Assess a statistical anomaly: an unusually large payment or a burst of payments from one customer. Use when the case category is anomaly."
---
# Anomaly indicators

The engine flagged a statistical outlier. Indicators are reasons to review, never findings of wrongdoing.

## Check
1. The rule that fired and its statistics (a robust z-score against the merchant's usual amounts, or the count of payments in a window).
2. The counterparty's history (`get_counterparty_history`, or delegate to pattern-analyst): is this unusual for them, or their normal pattern?
3. Whether the payments otherwise reconcile cleanly, and whether related cases (duplicates, refunds, other bursts) involve the same customer.
4. Ordinary explanations: a corporate or bulk order, a retried card, a promotion.
5. `search_policies` for anomaly indicators: the referral thresholds. Cite them.

## Usual outcomes
Refer for wider review when the policy's thresholds are met; otherwise record the indicator and the ordinary explanation. Never call anyone fraudulent.

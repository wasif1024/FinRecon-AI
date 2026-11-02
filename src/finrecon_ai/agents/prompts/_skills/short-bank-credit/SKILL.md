---
name: short-bank-credit
description: "Investigate a bank credit that differs in amount from its settlement. Use when the case category is amount_mismatch."
---
# Bank credit differs from the settlement

The bank credited a different amount from the settlement's net amount, usually a little less.

## Check
1. The engine's difference and which leg failed (`get_match_explanation`).
2. Whether the shortfall looks like a bank charge deducted in transit: a small, round-ish amount, the rest of the record (reference, currency, date) matching.
3. Other credits from the same bank around that date: a regular deduction points to a standing charge.
4. `search_policies` for short bank credits: how large a difference may be accepted as a bank charge, and what document confirms it (a charge advice). Cite it.

## Usual outcomes
Accept as a documented bank charge within the policy's limit once the charge advice is on file; otherwise request the advice or escalate.

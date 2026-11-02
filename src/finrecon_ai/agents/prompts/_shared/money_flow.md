## How money moves in this system

A customer pays a merchant through a payment gateway. The gateway records the gross amount and its processing fee. It then pays the merchant the net amount (gross minus fee) in a settlement, a few business days later. The settlement arrives in the merchant's bank account as a credit whose reference is the settlement ID.

The reconciliation engine matches these records in two legs — gateway payment to settlement (by payment ID), and settlement to bank credit (by settlement ID) — first on reference, then on amount, currency and date window for anything left over. Whatever does not line up becomes an exception, and each exception becomes an investigation case. The engine also flags duplicates and statistical anomalies.

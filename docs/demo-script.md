# Demo script

A walkthrough of about eight minutes, for a live demo or a recorded video. Everything runs on the demo snapshot:
synthetic data, real investigations the agents made earlier. Only scene 7 calls Claude (about $0.90), and it is
optional.

## Before you start

```bash
scripts/demo.sh          # rebuilds the Docker demo (asks for the demo users' password)
```

- Open http://localhost:3000 in one browser profile signed in as `manager@demo.finrecon.dev`, and a private window
  for `approver@demo.finrecon.dev`. Have http://localhost:8000/docs ready in a tab.
- Optional, to show traces: set `DOCKER_OTEL_ENDPOINT=http://otel-lgtm:4318` in `.env`, run
  `docker compose --profile otel up -d otel-lgtm`, then `scripts/demo.sh` again. Grafana is on :3001.
- Rehearse once: `scripts/demo.sh` resets everything, so you can practise the decisions and start clean.

| Case | Shows |
|---|---|
| CASE-000013 | Fee discrepancy: investigated, approved by a second manager, executed (now awaiting evidence) |
| CASE-000023 | Bank reference typo: manual match approved and recorded (resolved) |
| CASE-000005 | Anomaly: escalation rejected with a reason |
| CASE-000031, -000022, -000029, -000016 ... | Ten recommendations waiting for approval, one per kind of problem |
| 18 new cases | Untouched: assign, annotate, or investigate live |

## 1. The problem (0:00 to 0:45)

**Show:** the README banner, or say it over the overview page.

> Finance teams reconcile three sources that never quite agree: the bank statement, the payment gateway and the
> merchant settlements. Matching is mechanical; the expensive part is the exceptions. Someone has to work out why
> 2.53 pounds went missing, gather the evidence, find the policy that applies, and get a manager to sign off.
> FinRecon AI does the matching deterministically, has AI agents investigate the exceptions, and lets nothing
> happen without a human decision.

## 2. Overview (0:45 to 1:30) as the manager

**Click:** Overview.

> One reconciliation run over 762 records from the three sources. Match rate per leg, discrepancies per currency,
> never added across currencies, and 31 exceptions by category, each opened as a case.

Point at the cards: 18 new, 10 awaiting approval.

## 3. A finished investigation (1:30 to 3:30)

**Click:** Cases, filter Status: Awaiting evidence, open **CASE-000013**.

> A 2.53 GBP fee discrepancy between the gateway and the settlement.

Scroll slowly and name each part:

- **Recommendation:** "Request documentation", executed. The amount comes from the matching engine, not the model.
- **Findings:** verified facts, each linked to evidence (click an `E2` chip: it jumps to the source), hypotheses
  kept separate, and the outstanding requirement: get PayGate's itemised fee breakdown.
- **Evidence:** the policy passages it cited, by document, version and section, "Payment Processing Fee Policy v1
  › 2. Fee discrepancy thresholds".
- **Transactions and matching:** the engine's rule-by-rule result, and the original records shown as untrusted
  text.
- **Approval history:** approved by Demo Approver, a second manager, then carried out.
- **Audit trail:** every tool call, the recommendation, the request, the decision and the execution.

> The agents read; they never act. The only way anything happens is a recorded human decision, and then fixed code
> carries it out.

## 4. Separation of duties (3:30 to 4:15)

**Click:** Approvals (as the manager).

> The manager started these investigations, so they can see the queue but may not approve their own work.

Open any pending case: the Actions panel says another manager must decide.

## 5. Deciding (4:15 to 5:30) as the approver

**Switch** to the approver window. **Click:** Approvals, open **CASE-000016** (amount mismatch, 1.84 EUR).

> The proposal: ask the bank for its charge advice before accepting the shortfall as a bank charge. Policy allows
> it under two euros, but only with that confirmation.

Choose **Approve**, add a short comment, **Record decision**. The case moves to Awaiting evidence and the
recommendation shows Executed. Optionally open **CASE-000029** (payment burst) and **Request evidence** with a
comment.

## 6. Trust and audit (5:30 to 6:30) as the manager

**Click:** Audit trail, **Verify chain**.

> Every event is hash-chained; change one and the chain breaks at that event. Audit events and decisions are
> append-only in the database itself, and the API's database login cannot change the schema or read another
> tenant's rows.

Filter by `approval.decided` to show the decision you just made. **Click:** Reports, Outstanding exceptions,
Download CSV.

## 7. Optional: a live investigation (6:30 to 8:00, calls Claude, about $0.90)

As the manager, open one of the **new** cases (a fee discrepancy if you can), **Assign** it to yourself, then
**Run AI investigation**. Progress shows live; it takes about a minute.

> Three agents: investigation, risk analysis for anomalies and duplicates, and resolution. Each is a Deep Agent with
> its own tools, an operating manual and category playbooks, and the small checks run on a cheaper model.

When it pauses, show the new findings and recommendation, then decide it as the approver. If tracing is on, open
the run in LangSmith (agent view) and in Grafana (one trace from the dashboard click to every query).

## 8. Close (8:00)

> Deterministic matching, AI investigation with grounded evidence, human approval with separation of duties, and
> an audit trail you can verify. One API with REST, Connect and gRPC, a typed contract shared with the dashboard,
> PostgreSQL row-level security with a least-privilege login, evaluations in LangSmith, OpenTelemetry, and the
> whole stack in Docker.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Sign-in fails | `scripts/demo.sh` sets one password for all demo users; use that one |
| A decision says "already decided" | The demo was used before: `scripts/demo.sh` resets it |
| Live investigation fails at once | `ANTHROPIC_API_KEY` in `.env`; the case page shows the agent's error |
| Ports 3000 or 8000 busy | `scripts/start.sh` (local) is still running: stop it, or `docker compose --profile web down` |

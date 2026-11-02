"""Read-only agent tools against the test database: scope, content and evidence tracking."""

import uuid

import pytest
from sqlalchemy import select

from finrecon_ai.agents.tools import TOOLS, ToolContext, ToolError, run_tool
from finrecon_ai.db.enums import EvidenceType, ExceptionCategory, RoleName
from finrecon_ai.db.models import (
    InvestigationCase,
    InvestigationEvidence,
    ReconciliationException,
    Tenant,
    Transaction,
)


def context(session, tenant_id, category: ExceptionCategory) -> ToolContext:
    case, exc = session.execute(
        select(InvestigationCase, ReconciliationException)
        .join(ReconciliationException, ReconciliationException.id == InvestigationCase.exception_id)
        .where(InvestigationCase.tenant_id == tenant_id, ReconciliationException.category == category).limit(1)
    ).one()
    return ToolContext(session=session, tenant_id=tenant_id, case=case, exception=exc)


def call(ctx, name, **args):
    model = TOOLS[name].input_model
    full = {field: None for field in model.model_fields} | args
    return run_tool(ctx, TOOLS[name], full)


@pytest.fixture
def fee_ctx(session, world, case_ids):
    return context(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)


def test_case_tools_describe_the_case(fee_ctx):
    case = call(fee_ctx, "get_case")
    assert case["category"] == "fee_discrepancy" and case["case_number"].startswith("CASE-")

    txns = call(fee_ctx, "get_case_transactions")["transactions"]
    assert {t["source_type"] for t in txns} == {"payment_gateway", "merchant_settlement"}
    assert all("raw" in t for t in txns)

    matches = call(fee_ctx, "get_match_explanation")["matches"]
    rules = {r["rule"] for m in matches for r in m["explanation"].get("rules", [])}
    assert {"amount", "fee"} <= rules


def test_unmatched_case_says_it_was_never_paired(session, world, case_ids):
    ctx = context(session, world[0].id, ExceptionCategory.MISSING_SETTLEMENT)
    assert call(ctx, "get_match_explanation")["matches"] == []


def test_search_finds_counterpart_and_is_tenant_scoped(session, world, case_ids):
    tenant = world[0]
    ctx = context(session, tenant.id, ExceptionCategory.PARTIALLY_MATCHED)
    bank = next(t for t in call(ctx, "get_case_transactions")["transactions"] if t["source_type"] == "bank_statement")
    found = call(ctx, "search_transactions", amount=bank["amount"], currency=bank["currency"])["transactions"]
    assert bank["id"] in {t["id"] for t in found}

    other = Tenant(name="Other", slug="other-tools")
    session.add(other)
    session.flush()
    foreign = ToolContext(session=session, tenant_id=other.id, case=ctx.case, exception=ctx.exception)
    assert call(foreign, "search_transactions", amount=bank["amount"])["transactions"] == []


def test_search_needs_a_filter_and_valid_values(fee_ctx):
    with pytest.raises(ToolError, match="at least one filter"):
        call(fee_ctx, "search_transactions")
    with pytest.raises(ToolError, match="decimal"):
        call(fee_ctx, "search_transactions", amount="ten dollars")
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        call(fee_ctx, "search_transactions", date_from="01/10/2026")


def test_search_treats_wildcards_literally(fee_ctx):
    assert call(fee_ctx, "search_transactions", reference="%")["transactions"] == []


def test_counterparty_history_stats_per_currency(session, world, case_ids):
    ctx = context(session, world[0].id, ExceptionCategory.ANOMALY)
    payer = next(t["counterparty"] for t in call(ctx, "get_case_transactions")["transactions"] if t["counterparty"])
    history = call(ctx, "get_counterparty_history", counterparty=payer)
    assert history["found"] and history["per_currency"]
    for stats in history["per_currency"].values():
        assert stats["count"] >= 1 and float(stats["min"]) <= float(stats["median"]) <= float(stats["max"])


def test_similar_cases_exclude_current_case(fee_ctx):
    similar = call(fee_ctx, "find_similar_cases")
    ids = {c["case_id"] for c in similar["cases"]}
    assert similar["category"] == "fee_discrepancy" and ids
    assert str(fee_ctx.case.id) not in ids
    assert ids <= fee_ctx.seen["historical_case"]


def test_case_findings_let_a_later_agent_cite_earlier_evidence_by_readable_id(fee_ctx, session, world):
    """Regression: later agents cited cases and transactions an earlier agent recorded, by CASE-…/STL-…, and they
    were dropped as unverified."""
    other = session.scalars(select(InvestigationCase).where(InvestigationCase.tenant_id == fee_ctx.tenant_id,
                                                            InvestigationCase.id != fee_ctx.case.id)).first()
    analyst = world[2][RoleName.ANALYST].user_id
    session.add(InvestigationEvidence(tenant_id=fee_ctx.tenant_id, case_id=fee_ctx.case.id,
                                      evidence_type=EvidenceType.HISTORICAL_CASE, related_case_id=other.id,
                                      collected_by_user_id=analyst))
    txn = session.scalars(select(Transaction).where(Transaction.tenant_id == fee_ctx.tenant_id)).first()
    session.add(InvestigationEvidence(tenant_id=fee_ctx.tenant_id, case_id=fee_ctx.case.id,
                                      evidence_type=EvidenceType.TRANSACTION, transaction_id=txn.id,
                                      collected_by_user_id=analyst))
    session.flush()

    shown = call(fee_ctx, "get_case_findings")["evidence"]
    assert {e["related_case_number"] for e in shown} >= {other.case_number}
    assert {e["source_transaction_id"] for e in shown} >= {txn.source_transaction_id}
    assert fee_ctx.resolve("historical_case", other.case_number) == str(other.id)
    assert fee_ctx.resolve("transaction", txn.source_transaction_id) == str(txn.id)


def test_match_members_can_be_cited_by_source_id(fee_ctx):
    """Regression: an agent cited the matched settlement as STL-… from get_match_explanation and it was dropped."""
    matches = call(fee_ctx, "get_match_explanation")["matches"]
    members = [t for m in matches for t in m["transactions"]]
    assert members
    for t in members:
        assert fee_ctx.resolve("transaction", t["source_transaction_id"]) == t["id"]


def test_seen_tracks_only_returned_ids(fee_ctx, session):
    call(fee_ctx, "get_case_transactions")
    seen = fee_ctx.seen["transaction"]
    assert seen and all(session.get(Transaction, uuid.UUID(i)) for i in seen)
    assert len(seen) == 2


def test_tool_input_is_validated(fee_ctx):
    with pytest.raises(ToolError, match="invalid input"):
        run_tool(fee_ctx, TOOLS["get_counterparty_history"], {"counterparty": "x", "merchant": None, "extra": 1})

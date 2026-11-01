"""End-to-end: ingest the synthetic dataset, reconcile, and check every exception against the manifest."""

import time
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.cli import DEMO_SOURCES, seed_demo
from finrecon_ai.db.enums import ExceptionCategory, ImportBatchStatus, MatchClassification
from finrecon_ai.db.models import (
    FinancialSource,
    ReconciliationException,
    Transaction,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.reconciliation.service import ReconciliationError, run_reconciliation
from finrecon_ai.synthetic.generator import Scenario, generate, write_dataset

# What the engine must report for each planted scenario.
EXPECTED_CATEGORY = {
    Scenario.FEE_DISCREPANCY: ExceptionCategory.FEE_DISCREPANCY,
    Scenario.BANK_AMOUNT_MISMATCH: ExceptionCategory.AMOUNT_MISMATCH,
    Scenario.MISSING_SETTLEMENT: ExceptionCategory.MISSING_SETTLEMENT,
    Scenario.MISSING_BANK_CREDIT: ExceptionCategory.UNMATCHED,
    Scenario.DUPLICATE_BANK_CREDIT: ExceptionCategory.CONFIRMED_DUPLICATE,
    Scenario.DUPLICATE_PAYMENT: ExceptionCategory.POTENTIAL_DUPLICATE,
    Scenario.DELAYED_SETTLEMENT: ExceptionCategory.DATE_MISMATCH,
    Scenario.WRONG_BANK_REFERENCE: ExceptionCategory.PARTIALLY_MATCHED,
    Scenario.CURRENCY_MISMATCH: ExceptionCategory.CURRENCY_MISMATCH,
    Scenario.UNEXPLAINED_BANK_CREDIT: ExceptionCategory.UNMATCHED,
    Scenario.BANK_ONLY_CHARGE: ExceptionCategory.UNMATCHED,
}
NO_EXCEPTION = {Scenario.CLEAN, Scenario.FAILED_PAYMENT, Scenario.MALFORMED_RECORD}
assert set(EXPECTED_CATEGORY) | NO_EXCEPTION == set(Scenario), "every scenario needs an expectation"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    data = generate(seed=42, payments=1000)
    write_dataset(data, out)
    return out, data.manifest


@pytest.fixture
def ingested(session: Session, dataset):
    out, manifest = dataset
    tenant = seed_demo(session)
    batch_ids = []
    for name, *_, file_name in DEMO_SOURCES.values():
        source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                              FinancialSource.name == name))
        result = ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=file_name,
                             content=(out / file_name).read_bytes())
        batch_ids.append(result.batch.id)
    return tenant, batch_ids, manifest


def test_every_planted_problem_found_and_nothing_else(session, ingested):
    tenant, batch_ids, manifest = ingested
    run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids).run

    rows = session.execute(
        select(ReconciliationException.id, ReconciliationException.category, Transaction.source_transaction_id)
        .join(exception_transactions, exception_transactions.c.exception_id == ReconciliationException.id)
        .join(Transaction, Transaction.id == exception_transactions.c.transaction_id)
        .where(ReconciliationException.run_id == run.id)
    ).all()
    found: dict[uuid.UUID, tuple[ExceptionCategory, set[str]]] = {}
    for exc_id, category, source_txn_id in rows:
        found.setdefault(exc_id, (category, set()))[1].add(source_txn_id)

    expected = []
    for case in manifest["scenarios"]:
        scenario = Scenario(case["scenario"])
        if scenario in NO_EXCEPTION:
            continue
        ids = set(case["payment_ids"]) | set(case["settlement_ids"]) | set(case["bank_transaction_ids"])
        expected.append((scenario, EXPECTED_CATEGORY[scenario], ids))

    unexplained = dict(found)
    for scenario, category, case_ids in expected:
        hits = [exc_id for exc_id, (cat, ids) in unexplained.items() if cat == category and ids <= case_ids]
        assert len(hits) == 1, f"{scenario} {sorted(case_ids)}: expected one {category}, found {len(hits)}"
        del unexplained[hits[0]]
    assert unexplained == {}, f"false positives: {list(unexplained.values())[:5]}"


def test_clean_payment_chains_fully_matched(session, ingested):
    tenant, batch_ids, manifest = ingested
    run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids).run

    matched_ids = set(session.scalars(
        select(Transaction.source_transaction_id)
        .join(transaction_match_items, transaction_match_items.c.transaction_id == Transaction.id)
        .join(TransactionMatch, TransactionMatch.id == transaction_match_items.c.match_id)
        .where(TransactionMatch.run_id == run.id, TransactionMatch.classification == MatchClassification.MATCHED)
    ))
    planted = {pid for case in manifest["scenarios"] for pid in case["payment_ids"]}
    by_reference = dict(session.execute(
        select(Transaction.external_reference, Transaction.source_transaction_id)
        .where(Transaction.import_batch_id.in_(batch_ids), Transaction.external_reference.is_not(None))
    ).all())
    payments = session.scalars(
        select(Transaction.source_transaction_id).where(Transaction.import_batch_id.in_(batch_ids),
                                                        Transaction.source_transaction_id.like("pg_%"))
    ).all()

    clean = [p for p in payments if p not in planted]
    assert len(clean) == manifest["scenario_counts"][Scenario.CLEAN]
    for payment_id in clean:
        settlement_id = by_reference[payment_id]
        bank_id = by_reference[settlement_id]
        assert {payment_id, settlement_id, bank_id} <= matched_ids, payment_id


def test_matches_and_exceptions_persisted_with_links(session, ingested):
    tenant, batch_ids, _ = ingested
    run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids).run

    match_count = session.scalar(select(func.count()).select_from(TransactionMatch)
                                 .where(TransactionMatch.run_id == run.id))
    assert match_count == sum(sum(c.values()) for c in run.summary["matches"].values())
    items = session.scalar(select(func.count()).select_from(transaction_match_items)
                           .join(TransactionMatch, TransactionMatch.id == transaction_match_items.c.match_id)
                           .where(TransactionMatch.run_id == run.id))
    assert items == 2 * match_count

    orphan = session.scalar(
        select(func.count()).select_from(ReconciliationException)
        .outerjoin(exception_transactions, exception_transactions.c.exception_id == ReconciliationException.id)
        .where(ReconciliationException.run_id == run.id, exception_transactions.c.exception_id.is_(None))
    )
    assert orphan == 0
    explanation = session.scalar(select(TransactionMatch.explanation).where(TransactionMatch.run_id == run.id)
                                 .limit(1))
    assert {"leg", "summary"} <= explanation.keys()


def test_rerun_is_idempotent_and_deterministic(session, ingested):
    tenant, batch_ids, _ = ingested
    first = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids)
    again = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=list(reversed(batch_ids)))
    assert again.created is False and again.run.id == first.run.id

    forced = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids, idempotency_key="manual-2")
    assert forced.created and forced.run.summary == first.run.summary


def test_reconciles_within_target(session, ingested):
    tenant, batch_ids, _ = ingested
    started = time.perf_counter()
    run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids)
    assert time.perf_counter() - started < 10  # SRS §12: 1,000-record dataset within 10s


def test_rejects_failed_or_foreign_batches(session, ingested):
    tenant, batch_ids, _ = ingested
    with pytest.raises(ReconciliationError, match="not found"):
        run_reconciliation(session, tenant_id=uuid.uuid4(), import_batch_ids=batch_ids)

    source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id).limit(1))
    failed = ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name="bad", content=b"\xff").batch
    assert failed.status is ImportBatchStatus.FAILED
    with pytest.raises(ReconciliationError, match="not completed"):
        run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=[failed.id])

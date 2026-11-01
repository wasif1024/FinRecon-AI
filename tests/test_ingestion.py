"""Integration tests against the migrated test database."""

import json
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from finrecon_ai.audit import verify_chain
from finrecon_ai.cli import DEMO_SOURCES, seed_demo
from finrecon_ai.db.enums import ImportBatchStatus
from finrecon_ai.db.models import AuditEvent, FinancialSource, Transaction
from finrecon_ai.ingestion.service import IngestionError, ingest_file
from finrecon_ai.synthetic.generator import BANK_FILE, Scenario, generate, write_dataset


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    dataset = generate(seed=42, payments=1000)
    write_dataset(dataset, out)
    return out, dataset.manifest


@pytest.fixture
def demo(session: Session):
    tenant = seed_demo(session)
    sources = {
        key: session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                          FinancialSource.name == name))
        for key, (name, *_) in DEMO_SOURCES.items()
    }
    return tenant, sources


def ingest(session, demo, key, content, file_name="file"):
    tenant, sources = demo
    return ingest_file(session, tenant_id=tenant.id, source_id=sources[key].id, file_name=file_name,
                       content=content)


def test_imports_three_sources_within_target(session, demo, dataset_dir):
    out, manifest = dataset_dir
    for key, (*_, file_name) in DEMO_SOURCES.items():
        started = time.perf_counter()
        result = ingest(session, demo, key, (out / file_name).read_bytes(), file_name)
        elapsed = time.perf_counter() - started
        assert elapsed < 30, f"{key} took {elapsed:.1f}s"  # SRS §12: 1,000 records in under 30s

        expected = manifest["files"][DEMO_SOURCES[key][1].value]["records"]
        assert result.created
        assert result.batch.total_records == expected

    tenant, _ = demo
    total = session.scalar(select(func.count()).select_from(Transaction).where(Transaction.tenant_id == tenant.id))
    malformed = manifest["scenario_counts"][Scenario.MALFORMED_RECORD]
    assert total == sum(f["records"] for f in manifest["files"].values()) - malformed


def test_malformed_rows_rejected_with_row_numbers(session, demo, dataset_dir):
    out, manifest = dataset_dir
    batch = ingest(session, demo, "bank", (out / BANK_FILE).read_bytes()).batch

    assert batch.status is ImportBatchStatus.COMPLETED_WITH_ERRORS
    planted = {c["details"]["row"]: c["details"]["column"] for c in manifest["scenarios"]
               if c["scenario"] == Scenario.MALFORMED_RECORD}
    rejected = {e["row"]: e["errors"][0]["column"] for e in batch.validation_errors}
    assert rejected == planted


def test_same_file_twice_is_idempotent(session, demo, dataset_dir):
    out, _ = dataset_dir
    content = (out / BANK_FILE).read_bytes()
    first = ingest(session, demo, "bank", content)
    second = ingest(session, demo, "bank", content)

    assert not second.created
    assert second.batch.id == first.batch.id
    count = session.scalar(select(func.count()).select_from(Transaction))
    assert count == first.batch.accepted_records


def test_json_amounts_stay_exact_and_raw_payload_preserved(session, demo):
    content = json.dumps({"data": [{
        "id": "pg_x", "order_reference": "ORD-x", "created_at": "2026-10-01T10:00:00Z",
        "amount": 1234.5678, "fee": 0.1, "currency": "usd", "status": "succeeded",
    }]}).encode()
    ingest(session, demo, "gateway", content)

    txn = session.scalar(select(Transaction).where(Transaction.source_transaction_id == "pg_x"))
    assert txn.amount == Decimal("1234.5678")
    assert txn.fee_amount == Decimal("0.1")
    assert txn.currency == "USD"
    assert txn.raw_payload["amount"] == "1234.5678"
    assert txn.raw_payload["currency"] == "usd"


@pytest.mark.parametrize("key, content, message", [
    ("bank", b"transaction_id,amount\nBNK-1,10.00\n", "missing required columns"),
    ("gateway", b"{not json", "invalid JSON"),
    ("gateway", b'{"rows": []}', "expected an object"),
    ("bank", b"\xff\xfe\x00", "UTF-8"),
])
def test_malformed_file_fails_batch(session, demo, key, content, message):
    batch = ingest(session, demo, key, content).batch
    assert batch.status is ImportBatchStatus.FAILED
    assert message in batch.validation_errors[0]["errors"][0]["error"]
    assert batch.accepted_records == 0


def test_source_from_other_tenant_rejected(session, demo):
    import uuid

    _, sources = demo
    with pytest.raises(IngestionError, match="not found"):
        ingest_file(session, tenant_id=uuid.uuid4(), source_id=sources["bank"].id, file_name="f", content=b"x")


def test_each_import_is_audited_with_intact_chain(session, demo, dataset_dir):
    out, _ = dataset_dir
    tenant, _ = demo
    for key, (*_, file_name) in DEMO_SOURCES.items():
        ingest(session, demo, key, (out / file_name).read_bytes())

    events = session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id)).all()
    assert [e.event_type for e in events] == ["ingestion.batch_completed"] * 3
    session.expire_all()
    assert verify_chain(session, tenant.id) is None


def test_audit_events_cannot_be_modified(session, demo):
    ingest(session, demo, "gateway", b'{"data": []}')
    with pytest.raises(DBAPIError, match="append-only"):
        with session.begin_nested():
            session.execute(update(AuditEvent).values(event_type="tampered"))

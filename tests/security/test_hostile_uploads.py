"""Hostile and malformed files: every one must be refused cleanly (never a crash, never partial data)."""

import io
import json
import zipfile

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from finrecon_ai.cli import DEMO_SOURCES
from finrecon_ai.db.enums import ImportBatchStatus, PolicyDocumentStatus, RoleName
from finrecon_ai.db.models import FinancialSource, Transaction
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.policies.parsing import DocumentRejected, parse_document
from tests.test_api import auth, client, scripts  # noqa: F401 (fixtures)
from tests.test_workflows import store  # noqa: F401 (fixture)

BANK_HEADER = "transaction_id,booking_date,value_date,description,reference,amount,currency,counterparty\n"


def source(session, world, key):
    name = DEMO_SOURCES[key][0]
    return session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == world[0].id,
                                                        FinancialSource.name == name))


def ingest(session, world, key, content):
    return ingest_file(session, tenant_id=world[0].id, source_id=source(session, world, key).id,
                       file_name="hostile", content=content).batch


@pytest.mark.parametrize("key, content, reason", [
    ("gateway", b'{"data": ' + b"[" * 100_000 + b"]" * 100_000 + b"}", "nested too deeply"),
    ("bank", (BANK_HEADER + "B1,2026-10-01,,x,r," + "9" * 200_000 + ",USD,c\n").encode(), "malformed CSV"),
    ("bank", (BANK_HEADER + "B1,2026-10-01,,x\x00y,r,1.00,USD,c\n").encode(), "NUL"),
    ("bank", b"\xff\xfe" + "transaction_id".encode("utf-16-le"), "UTF-8"),
    ("gateway", b'{"data": "not a list"}', "expected an object"),
])
def test_hostile_files_fail_the_batch_cleanly(session, world, key, content, reason):
    batch = ingest(session, world, key, content)
    assert batch.status is ImportBatchStatus.FAILED
    assert reason in batch.validation_errors[0]["errors"][0]["error"]
    assert session.scalar(select(func.count()).select_from(Transaction)
                          .where(Transaction.import_batch_id == batch.id)) == 0


def test_escaped_nul_in_json_rejects_only_that_record(session, world):
    records = [{"id": "pg_ok", "created_at": "2026-10-01T10:00:00Z", "amount": 10, "currency": "USD",
                "status": "succeeded"},
               {"id": "pg_bad", "created_at": "2026-10-01T10:00:00Z", "amount": 10, "currency": "USD",
                "status": "succeeded", "description": "evil\u0000payload"}]
    batch = ingest(session, world, "gateway", json.dumps({"data": records}).encode())
    assert (batch.accepted_records, batch.rejected_records) == (1, 1)
    assert "NUL" in batch.validation_errors[0]["errors"][0]["error"]


def test_formula_like_values_are_stored_as_plain_text(session, world):
    """Spreadsheet formulas are data here; CSV exports (Phase 5) must neutralise them on the way out."""
    content = (BANK_HEADER + 'B1,2026-10-01,,"=HYPERLINK(""http://evil"")",r,1.00,USD,"@SUM(A1)"\n').encode()
    batch = ingest(session, world, "bank", content)
    txn = session.scalar(select(Transaction).where(Transaction.import_batch_id == batch.id))
    assert txn.description.startswith("=HYPERLINK") and txn.counterparty == "@SUM(A1)"


# --- policy documents ---


def encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def zip_bomb_docx(monkeypatch) -> bytes:
    from finrecon_ai.policies import parsing

    monkeypatch.setattr(parsing, "MAX_DOCX_UNCOMPRESSED", 1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * (5 * 1024 * 1024))  # compresses to a few KB
    return buffer.getvalue()


@pytest.mark.parametrize("name, make, reason", [
    ("p.pdf", lambda mp: encrypted_pdf(), "encrypted"),
    ("p.docx", zip_bomb_docx, "unreasonable size"),
    ("p.pdf", lambda mp: b"%PDF-1.7\n" + b"\x00garbage" * 100, "could not read PDF"),
    ("p.svg", lambda mp: b"<svg onload=alert(1)>", "unsupported file type"),
    ("p.md.exe", lambda mp: b"MZ", "unsupported file type"),
])
def test_hostile_policy_documents_are_refused(monkeypatch, name, make, reason):
    with pytest.raises(DocumentRejected, match=reason):
        parse_document(name, make(monkeypatch))


def test_policy_file_name_cannot_choose_storage_path(client, tmp_path, monkeypatch):
    from finrecon_ai import config

    storage = tmp_path / "storage"
    monkeypatch.setattr(config.get_settings(), "document_storage_dir", str(storage))
    response = client.post("/policies", files={"file": ("../../../../etc/evil.md", b"# Evil\ntext")},
                           headers=auth(client, RoleName.ADMIN))
    assert response.status_code == 201 and response.json()["status"] == PolicyDocumentStatus.INDEXED.value
    stored = [p for p in tmp_path.rglob("*") if p.is_file() and "evil" in p.read_text(errors="ignore").lower()]
    assert len(stored) == 1 and storage in stored[0].parents, "stored under the storage dir, named by hash"
    assert "evil" not in stored[0].name


# --- through the API ---


def test_oversized_upload_rejected_before_parsing(client, session, world, monkeypatch):
    from finrecon_ai.api.routes import sources

    monkeypatch.setattr(sources, "MAX_FILE_BYTES", 100)
    response = client.post(f"/sources/{source(session, world, 'bank').id}/imports",
                           files={"file": ("big.csv", b"x" * 1000)}, headers=auth(client, RoleName.ANALYST))
    assert response.status_code == 413


def test_hostile_file_over_api_is_a_failed_batch_not_a_crash(client, session, world):
    response = client.post(f"/sources/{source(session, world, 'gateway').id}/imports",
                           files={"file": ("deep.json", b'{"data": ' + b"[" * 50_000 + b"]" * 50_000 + b"}")},
                           headers=auth(client, RoleName.ANALYST))
    assert response.status_code == 201 and response.json()["status"] == "failed"

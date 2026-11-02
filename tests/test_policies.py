"""Policy documents: parsing, chunking, Qdrant indexing and search, versioning, permissions, agent tool."""

import io
import uuid
from pathlib import Path

import pytest
from docx import Document
from qdrant_client import QdrantClient
from sqlalchemy import select

from finrecon_ai.agents.tools import TOOLS, ToolContext, ToolError, run_tool
from finrecon_ai.authz import Actor, PermissionDenied
from finrecon_ai.db.enums import PolicyDocumentStatus, RoleName
from finrecon_ai.db.models import AuditEvent, InvestigationCase, ReconciliationException
from finrecon_ai.policies.chunking import chunk_sections
from finrecon_ai.policies.embedding import HashingEmbedder
from finrecon_ai.policies.parsing import DocumentRejected, Section, parse_document
from finrecon_ai.policies.service import list_policies, search_policies, upload_policy
from finrecon_ai.policies.store import PolicyStore

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "policies"


@pytest.fixture
def store():
    return PolicyStore(QdrantClient(":memory:"), HashingEmbedder())


# --- parsing ---


def test_markdown_keeps_heading_trail():
    doc = parse_document("p.md", b"# Fee Policy\nintro\n## 2. Thresholds\nrules\n### 2.1 Repeats\nthree or more\n")
    assert doc.title == "Fee Policy"
    assert [s.path for s in doc.sections] == [("Fee Policy",), ("Fee Policy", "2. Thresholds"),
                                              ("Fee Policy", "2. Thresholds", "2.1 Repeats")]


def test_plain_text_uses_first_line_as_title_and_numbered_headings():
    doc = parse_document("p.txt", b"Investigation Procedure\n\n1. Purpose\nWhy.\n2.1 Detail\nMore.\n")
    assert doc.title == "Investigation Procedure"
    assert doc.sections[-1].path == ("Investigation Procedure", "1. Purpose", "2.1 Detail")


def test_docx_headings_and_tables():
    document = Document()
    document.add_heading("Settlement Policy", level=0)
    document.add_heading("1. Timing", level=1)
    document.add_paragraph("T+2.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Late credit", "> 5 days"
    buffer = io.BytesIO()
    document.save(buffer)

    doc = parse_document("p.docx", buffer.getvalue())
    assert doc.title == "Settlement Policy"
    last = doc.sections[-1]
    assert last.path == ("Settlement Policy", "1. Timing") and "Late credit | > 5 days" in last.text


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.iterdir()))
def test_sample_policies_parse_with_sections(name):
    doc = parse_document(name, (SAMPLES / name).read_bytes())
    assert len(doc.sections) >= 5 and all(s.path and s.path[0] == doc.title for s in doc.sections)
    if name.endswith(".pdf"):
        assert all(s.page == 1 for s in doc.sections)


@pytest.mark.parametrize("name, content, message", [
    ("p.exe", b"MZ", "unsupported file type"),
    ("p.md", b"", "empty"),
    ("p.pdf", b"not a pdf", "not a valid PDF"),
    ("p.docx", b"PK\x03\x04garbage", "could not read DOCX"),
    ("p.docx", b"plain text", "not a valid DOCX"),
    ("p.txt", b"\xff\xfe\x00bad", "UTF-8"),
    ("p.md", b"   \n\n  ", "no readable text"),
])
def test_rejects_bad_files(name, content, message):
    with pytest.raises(DocumentRejected, match=message):
        parse_document(name, content)


def test_rejects_oversized_file(monkeypatch):
    from finrecon_ai.policies import parsing

    monkeypatch.setattr(parsing, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(DocumentRejected, match="exceeds"):
        parse_document("p.md", b"# Title\n" + b"x" * 20)


# --- chunking ---


def test_long_sections_split_within_limit_and_never_cross_sections():
    long_text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    sections = [Section(("Doc", "A"), long_text), Section(("Doc", "B"), "short")]
    chunks = chunk_sections(sections, max_chars=500, overlap=50)

    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 500 for c in chunks)
    assert chunks[-1].section_path == ("Doc", "B") and chunks[-1].text == "short"
    a_chunks = [c for c in chunks if c.section_path == ("Doc", "A")]
    assert len(a_chunks) > 1
    assert "Paragraph 9" in a_chunks[-1].text


def test_embedding_text_includes_section():
    [chunk] = chunk_sections([Section(("Fee Policy", "2. Thresholds"), "Up to 0.50")])
    assert chunk.embedding_text.startswith("Fee Policy › 2. Thresholds")


# --- store ---


def test_search_is_tenant_scoped_and_ranked(store):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    fee = chunk_sections([Section(("Fees",), "processing fee variance thresholds and approval")])
    late = chunk_sections([Section(("Settlement",), "late bank credit escalation after five business days")])
    store.index(tenant_id=tenant_a, document_id=uuid.uuid4(), title="Fees", version=1, chunks=fee)
    store.index(tenant_id=tenant_a, document_id=uuid.uuid4(), title="Settlement", version=1, chunks=late)
    store.index(tenant_id=tenant_b, document_id=uuid.uuid4(), title="Other", version=1, chunks=fee)

    hits = store.search(tenant_id=tenant_a, query="processing fee variance", limit=5)
    assert [h.title for h in hits][0] == "Fees"
    assert {h.title for h in hits} <= {"Fees", "Settlement"}, "tenant B's document must never appear"
    assert store.search(tenant_id=uuid.uuid4(), query="fee", limit=5) == []


def test_reindex_is_idempotent_and_delete_removes(store):
    tenant, doc_id = uuid.uuid4(), uuid.uuid4()
    chunks = chunk_sections([Section(("Doc",), "duplicate bank credit handling")])
    first = store.index(tenant_id=tenant, document_id=doc_id, title="Doc", version=1, chunks=chunks)
    second = store.index(tenant_id=tenant, document_id=doc_id, title="Doc", version=1, chunks=chunks)
    assert first == second
    assert len(store.search(tenant_id=tenant, query="duplicate", limit=10)) == 1
    store.delete_document(doc_id)
    assert store.search(tenant_id=tenant, query="duplicate", limit=10) == []


def test_collection_is_per_embedding_model(store):
    assert store.collection == "policy_chunks__hashing_256"


# --- service ---


def upload(session, actor, store, tmp_path, name, content):
    return upload_policy(session, actor, store, file_name=name, content=content, storage_dir=tmp_path)


def test_admin_uploads_and_original_is_stored(session, world, store, tmp_path):
    admin = world[2][RoleName.ADMIN]
    content = (SAMPLES / "payment_processing_fee_policy.md").read_bytes()
    result = upload(session, admin, store, tmp_path, "payment_processing_fee_policy.md", content)

    doc = result.document
    assert result.created and doc.status is PolicyDocumentStatus.INDEXED
    assert doc.title == "Payment Processing Fee Policy" and doc.version == 1 and doc.chunk_count == 6
    assert Path(doc.storage_uri).read_bytes() == content
    assert session.scalar(select(AuditEvent.event_type).where(AuditEvent.entity_id == str(doc.id))) == "policy.indexed"

    again = upload(session, admin, store, tmp_path, "renamed.md", content)
    assert not again.created and again.document.id == doc.id


@pytest.mark.parametrize("role", [RoleName.ANALYST, RoleName.MANAGER, RoleName.AUDITOR])
def test_only_admins_upload(session, world, store, tmp_path, role):
    with pytest.raises(PermissionDenied):
        upload(session, world[2][role], store, tmp_path, "p.md", b"# T\ntext")


def test_new_version_by_title_replaces_old_in_search(session, world, store, tmp_path):
    admin, analyst = world[2][RoleName.ADMIN], world[2][RoleName.ANALYST]
    v1 = upload(session, admin, store, tmp_path, "fees.md", b"# Fee Rules\nvariance up to 0.50 accepted").document
    v2 = upload(session, admin, store, tmp_path, "fees-2.md", b"# Fee Rules\nvariance up to 1.00 accepted").document

    assert (v1.version, v2.version) == (1, 2)
    assert v1.status is PolicyDocumentStatus.ARCHIVED
    hits = search_policies(analyst, store, "fee variance accepted")
    assert {h.version for h in hits} == {2} and "1.00" in hits[0].text
    assert [d.version for d in list_policies(session, analyst)] == [2]
    assert len(list_policies(session, analyst, include_archived=True)) == 2


def test_unreadable_file_is_recorded_as_failed(session, world, store, tmp_path):
    result = upload(session, world[2][RoleName.ADMIN], store, tmp_path, "broken.pdf", b"not a pdf at all")
    assert result.document.status is PolicyDocumentStatus.FAILED
    assert "not a valid PDF" in result.document.error


def test_search_requires_read_permission(world, store):
    with pytest.raises(PermissionDenied):
        search_policies(Actor.system(world[0].id), store, "fees")


# --- agent tool and identifiers ---


@pytest.fixture
def tool_ctx(session, world, case_ids, store):
    case = session.get(InvestigationCase, case_ids[0])
    return ToolContext(session=session, tenant_id=world[0].id, case=case,
                       exception=session.get(ReconciliationException, case.exception_id), policy_store=store)


def test_search_policies_tool_remembers_chunks(session, world, store, tmp_path, tool_ctx):
    upload(session, world[2][RoleName.ADMIN], store, tmp_path, "fees.md",
           (SAMPLES / "payment_processing_fee_policy.md").read_bytes())
    result = run_tool(tool_ctx, TOOLS["search_policies"], {"query": "fee discrepancy thresholds", "limit": 3})

    passages = result["passages"]
    assert passages and passages[0]["document"] == "Payment Processing Fee Policy"
    assert {p["chunk_id"] for p in passages} == tool_ctx.seen["policy_passage"]
    assert tool_ctx.resolve("policy_passage", passages[0]["chunk_id"]) == passages[0]["chunk_id"]


def test_search_policies_tool_without_store(tool_ctx):
    tool_ctx.policy_store = None
    with pytest.raises(ToolError, match="not available"):
        run_tool(tool_ctx, TOOLS["search_policies"], {"query": "fees", "limit": None})


def test_identifiers_resolve_by_any_shown_alias(tool_ctx):
    run_tool(tool_ctx, TOOLS["get_case_transactions"], {})
    txn = run_tool(tool_ctx, TOOLS["get_case_transactions"], {})["transactions"][0]
    assert tool_ctx.resolve("transaction", txn["id"]) == txn["id"]
    assert tool_ctx.resolve("transaction", txn["source_transaction_id"]) == txn["id"]
    assert tool_ctx.resolve("transaction", "pg_never_seen") is None


def test_alias_shared_by_two_records_is_ambiguous(tool_ctx):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    tool_ctx.remember("transaction", a, "pg_000001")
    tool_ctx.remember("transaction", b, "pg_000001")
    assert tool_ctx.resolve("transaction", "pg_000001") is None
    assert tool_ctx.resolve("transaction", a) == a and tool_ctx.seen["transaction"] == {a, b}

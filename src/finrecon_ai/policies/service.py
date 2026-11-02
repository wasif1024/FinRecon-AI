"""Policy document management (FR-07): upload, parse, chunk, embed, index, version, search.

A document with the same title as an indexed one becomes its next version; the previous version is archived
and its chunks removed from the index, so search only ever returns current policy. The caller commits.
"""

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.enums import ActorType, PolicyDocumentStatus
from finrecon_ai.db.models import PolicyDocument
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.log import get_logger
from finrecon_ai.policies.chunking import chunk_sections
from finrecon_ai.policies.parsing import DocumentRejected, parse_document
from finrecon_ai.policies.store import PolicyHit, PolicyStore

log = get_logger(__name__)


@dataclass(frozen=True)
class UploadResult:
    document: PolicyDocument
    created: bool  # False when this exact file was already uploaded


def upload_policy(session: Session, actor: Actor, store: PolicyStore, *, file_name: str, content: bytes,
                  storage_dir: Path, title: str | None = None) -> UploadResult:
    actor.require(Permission.POLICY_MANAGE)
    bind_tenant(session, actor.tenant_id)
    file_sha256 = hashlib.sha256(content).hexdigest()
    existing = session.scalar(select(PolicyDocument).where(PolicyDocument.tenant_id == actor.tenant_id,
                                                           PolicyDocument.file_sha256 == file_sha256))
    if existing is not None:
        log.info("policy.skipped_duplicate_file", document_id=existing.id)
        return UploadResult(existing, created=False)

    started = time.perf_counter()
    suffix = PurePath(file_name).suffix.lower()
    stored = storage_dir / str(actor.tenant_id) / f"{file_sha256}{suffix}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(content)

    document = PolicyDocument(
        tenant_id=actor.tenant_id, title=title or PurePath(file_name).stem, file_name=file_name,
        mime_type="application/octet-stream", file_sha256=file_sha256, storage_uri=str(stored),
        status=PolicyDocumentStatus.PROCESSING, qdrant_collection=store.collection, uploaded_by=actor.user_id,
    )
    session.add(document)
    session.flush()

    try:
        parsed = parse_document(file_name, content)
    except DocumentRejected as exc:
        document.status = PolicyDocumentStatus.FAILED
        document.error = str(exc)
        session.flush()
        log.warning("policy.rejected", document_id=document.id, reason=str(exc))
        _audit(session, actor, document, "policy.upload_failed", {"file_name": file_name, "error": str(exc)})
        return UploadResult(document, created=True)

    document.title = (title or parsed.title)[:300]
    document.mime_type = parsed.mime_type
    previous = session.scalar(
        select(PolicyDocument).where(PolicyDocument.tenant_id == actor.tenant_id,
                                     func.lower(PolicyDocument.title) == document.title.lower(),
                                     PolicyDocument.status == PolicyDocumentStatus.INDEXED,
                                     PolicyDocument.id != document.id)
        .order_by(PolicyDocument.version.desc()).limit(1)
    )
    document.version = previous.version + 1 if previous else 1

    chunks = chunk_sections(parsed.sections)
    store.index(tenant_id=actor.tenant_id, document_id=document.id, title=document.title,
                version=document.version, chunks=chunks)
    document.status = PolicyDocumentStatus.INDEXED
    document.chunk_count = len(chunks)
    document.indexed_at = datetime.now(UTC)
    if previous is not None:
        store.delete_document(previous.id)
        previous.status = PolicyDocumentStatus.ARCHIVED
    session.flush()

    log.info("policy.indexed", document_id=document.id, version=document.version, sections=len(parsed.sections),
             chunks=len(chunks), replaced=previous.id if previous else None,
             duration_ms=round((time.perf_counter() - started) * 1000, 1))
    _audit(session, actor, document, "policy.indexed", {
        "title": document.title, "version": document.version, "file_name": file_name, "file_sha256": file_sha256,
        "chunks": len(chunks), "replaced_document_id": previous.id if previous else None,
    })
    return UploadResult(document, created=True)


def list_policies(session: Session, actor: Actor, *, include_archived: bool = False) -> list[PolicyDocument]:
    actor.require(Permission.POLICY_READ)
    bind_tenant(session, actor.tenant_id)
    query = select(PolicyDocument).where(PolicyDocument.tenant_id == actor.tenant_id)
    if not include_archived:
        query = query.where(PolicyDocument.status != PolicyDocumentStatus.ARCHIVED)
    return list(session.scalars(query.order_by(PolicyDocument.title, PolicyDocument.version)))


def search_policies(actor: Actor, store: PolicyStore, query: str, limit: int = 5) -> list[PolicyHit]:
    actor.require(Permission.POLICY_READ)
    return store.search(tenant_id=actor.tenant_id, query=query, limit=limit)


def _audit(session: Session, actor: Actor, document: PolicyDocument, event_type: str, payload: dict) -> None:
    record_event(session, tenant_id=actor.tenant_id, event_type=event_type, entity_type="policy_document",
                 entity_id=document.id, actor_type=ActorType.SYSTEM if actor.is_system else ActorType.USER,
                 actor_user_id=actor.user_id, payload=payload)

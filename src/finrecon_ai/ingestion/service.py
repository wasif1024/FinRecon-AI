"""Import a file into an import batch: parse, validate, normalize, store (FR-01, FR-02).

The caller owns the transaction and commits.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType, ImportBatchStatus, IngestionMethod
from finrecon_ai.db.models import FinancialSource, ImportBatch, Transaction
from finrecon_ai.ingestion.mappings import SourceMapping
from finrecon_ai.ingestion.normalizer import RecordRejected, fingerprint, normalize_record
from finrecon_ai.ingestion.parsers import FileRejected, contains_nul, parse_file, to_json_safe
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.errors import NotFound
from finrecon_ai.log import get_logger

log = get_logger(__name__)

MAX_STORED_ERRORS = 1000


class IngestionError(Exception):
    pass


class SourceNotFound(NotFound, IngestionError):
    pass


@dataclass(frozen=True)
class IngestResult:
    batch: ImportBatch
    created: bool  # False when the same file was already imported for this source


def ingest_file(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    file_name: str,
    content: bytes,
    storage_uri: str | None = None,
    uploaded_by: uuid.UUID | None = None,
) -> IngestResult:
    bind_tenant(session, tenant_id)
    source = session.scalar(
        select(FinancialSource).where(FinancialSource.id == source_id, FinancialSource.tenant_id == tenant_id)
    )
    if source is None:
        raise SourceNotFound("financial source not found")
    if not source.is_active:
        raise IngestionError(f"financial source {source.name!r} is inactive")
    if source.ingestion_method not in (IngestionMethod.CSV, IngestionMethod.JSON):
        raise IngestionError(f"source {source.name!r} does not accept file uploads")

    file_sha256 = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(ImportBatch).where(
            ImportBatch.tenant_id == tenant_id,
            ImportBatch.source_id == source_id,
            ImportBatch.file_sha256 == file_sha256,
        )
    )
    if existing is not None:
        log.info("ingestion.skipped_duplicate_file", source_id=source_id, batch_id=existing.id)
        return IngestResult(batch=existing, created=False)

    mapping = SourceMapping.model_validate(source.config["mapping"])
    batch = ImportBatch(
        tenant_id=tenant_id,
        source_id=source_id,
        file_name=file_name,
        file_format=source.ingestion_method,
        file_sha256=file_sha256,
        storage_uri=storage_uri,
        status=ImportBatchStatus.PROCESSING,
        uploaded_by=uploaded_by,
        started_at=datetime.now(UTC),
    )
    session.add(batch)
    session.flush()
    started = time.perf_counter()
    log.info("ingestion.started", source_id=source_id, batch_id=batch.id, file_format=source.ingestion_method,
             size_bytes=len(content))

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total = 0
    try:
        for row_number, raw in parse_file(content, source.ingestion_method, mapping):
            total += 1
            if contains_nul(raw):
                errors.append({"row": row_number, "errors": [{"field": None, "column": None,
                                                              "error": "record contains a NUL character"}]})
                continue
            try:
                txn = normalize_record(raw, mapping)
            except RecordRejected as exc:
                errors.append(
                    {
                        "row": row_number,
                        "errors": [{"field": e.field, "column": e.column, "error": e.error} for e in exc.errors],
                        "raw": to_json_safe(raw),
                    }
                )
                continue
            rows.append(
                {
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "import_batch_id": batch.id,
                    "source_row_number": row_number,
                    "fingerprint": fingerprint(source_id, txn),
                    "raw_payload": to_json_safe(raw),
                    **vars(txn),
                }
            )
    except FileRejected as exc:
        batch.status = ImportBatchStatus.FAILED
        batch.validation_errors = [{"row": None, "errors": [{"error": str(exc)}]}]
        log.warning("ingestion.file_rejected", batch_id=batch.id, reason=str(exc))
        return _finish(session, batch, uploaded_by)

    if rows:
        session.execute(insert(Transaction), rows)

    batch.total_records = total
    batch.accepted_records = len(rows)
    batch.rejected_records = len(errors)
    batch.validation_errors = errors[:MAX_STORED_ERRORS]
    if not rows and total:
        batch.status = ImportBatchStatus.FAILED
    elif errors:
        batch.status = ImportBatchStatus.COMPLETED_WITH_ERRORS
    else:
        batch.status = ImportBatchStatus.COMPLETED
    log.info("ingestion.completed", batch_id=batch.id, status=batch.status, total=total, accepted=len(rows),
             rejected=len(errors), duration_ms=round((time.perf_counter() - started) * 1000, 1))
    if errors:
        log.warning("ingestion.rows_rejected", batch_id=batch.id, rejected=len(errors),
                    first_rows=[e["row"] for e in errors[:10]])
    return _finish(session, batch, uploaded_by)


def _finish(session: Session, batch: ImportBatch, uploaded_by: uuid.UUID | None) -> IngestResult:
    batch.completed_at = datetime.now(UTC)
    session.flush()
    record_event(
        session,
        tenant_id=batch.tenant_id,
        event_type="ingestion.batch_completed",
        entity_type="import_batch",
        entity_id=batch.id,
        actor_type=ActorType.USER if uploaded_by else ActorType.SYSTEM,
        actor_user_id=uploaded_by,
        payload={
            "source_id": batch.source_id,
            "file_name": batch.file_name,
            "file_sha256": batch.file_sha256,
            "status": batch.status,
            "total_records": batch.total_records,
            "accepted_records": batch.accepted_records,
            "rejected_records": batch.rejected_records,
        },
    )
    return IngestResult(batch=batch, created=True)

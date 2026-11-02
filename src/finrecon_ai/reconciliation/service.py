"""Run the matching engine over import batches and persist the outcome. The caller commits."""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType, ImportBatchStatus, RunStatus
from finrecon_ai.db.models import (
    FinancialSource,
    ImportBatch,
    ReconciliationException,
    ReconciliationRun,
    Transaction,
    TransactionMatch,
    exception_transactions,
    transaction_match_items,
)
from finrecon_ai.reconciliation.config import DEFAULT_CONFIG, ENGINE_VERSION, ReconciliationConfig
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.errors import NotFound
from finrecon_ai.log import get_logger
from finrecon_ai.reconciliation.engine import Txn, reconcile

log = get_logger(__name__)

RECONCILABLE_BATCH_STATUSES = (ImportBatchStatus.COMPLETED, ImportBatchStatus.COMPLETED_WITH_ERRORS)


class ReconciliationError(Exception):
    pass


class BatchNotFound(NotFound, ReconciliationError):
    pass


@dataclass(frozen=True)
class RunResult:
    run: ReconciliationRun
    created: bool  # False when an identical run (same batches, config, engine) already exists


def run_reconciliation(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    import_batch_ids: list[uuid.UUID],
    config: ReconciliationConfig = DEFAULT_CONFIG,
    started_by: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> RunResult:
    bind_tenant(session, tenant_id)
    batch_ids = sorted(set(import_batch_ids), key=str)
    if not batch_ids:
        raise ReconciliationError("no import batches given")
    batches = session.scalars(
        select(ImportBatch).where(ImportBatch.id.in_(batch_ids), ImportBatch.tenant_id == tenant_id)
    ).all()
    if len(batches) != len(batch_ids):
        raise BatchNotFound("import batch not found")
    not_ready = [str(b.id) for b in batches if b.status not in RECONCILABLE_BATCH_STATUSES]
    if not_ready:
        raise ReconciliationError(f"import batches not completed: {not_ready}")

    config_snapshot = config.model_dump(mode="json")
    key = idempotency_key or _default_key(batch_ids, config_snapshot)
    existing = session.scalar(
        select(ReconciliationRun).where(ReconciliationRun.tenant_id == tenant_id,
                                        ReconciliationRun.idempotency_key == key)
    )
    if existing is not None:
        log.info("reconciliation.reused_existing_run", run_id=existing.id)
        return RunResult(existing, created=False)

    started_at = datetime.now(UTC)
    clock = time.perf_counter()
    rows = session.execute(
        select(Transaction, FinancialSource.source_type)
        .join(FinancialSource, FinancialSource.id == Transaction.source_id)
        .where(Transaction.tenant_id == tenant_id, Transaction.import_batch_id.in_(batch_ids))
    ).all()
    result = reconcile((_to_txn(t, source_type) for t, source_type in rows), config)
    engine_ms = round((time.perf_counter() - clock) * 1000, 1)

    run = ReconciliationRun(
        tenant_id=tenant_id,
        idempotency_key=key,
        status=RunStatus.COMPLETED,
        config=config_snapshot,
        engine_version=ENGINE_VERSION,
        import_batch_ids=[str(b) for b in batch_ids],
        summary=result.summary,
        started_by=started_by,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()

    match_ids = [uuid.uuid4() for _ in result.matches]
    if result.matches:
        session.execute(insert(TransactionMatch), [
            {"id": match_id, "tenant_id": tenant_id, "run_id": run.id, "classification": m.classification,
             "strategy": m.strategy, "score": m.score, "amount_difference": m.amount_difference,
             "currency": m.currency, "explanation": m.explanation}
            for match_id, m in zip(match_ids, result.matches, strict=True)
        ])
        session.execute(insert(transaction_match_items), [
            {"match_id": match_id, "transaction_id": txn_id}
            for match_id, m in zip(match_ids, result.matches, strict=True) for txn_id in m.transaction_ids
        ])

    exception_ids = [uuid.uuid4() for _ in result.exceptions]
    if result.exceptions:
        session.execute(insert(ReconciliationException), [
            {"id": exc_id, "tenant_id": tenant_id, "run_id": run.id,
             "match_id": match_ids[e.match_index] if e.match_index is not None else None,
             "category": e.category, "severity": e.severity, "discrepancy_amount": e.discrepancy_amount,
             "currency": e.currency, "rule_code": e.rule_code, "details": _json_safe(e.details)}
            for exc_id, e in zip(exception_ids, result.exceptions, strict=True)
        ])
        session.execute(insert(exception_transactions), [
            {"exception_id": exc_id, "transaction_id": txn_id}
            for exc_id, e in zip(exception_ids, result.exceptions, strict=True) for txn_id in e.transaction_ids
        ])

    log.info("reconciliation.completed", run_id=run.id, engine_version=ENGINE_VERSION, transactions=len(rows),
             matches=len(result.matches), exceptions=len(result.exceptions), engine_ms=engine_ms,
             duration_ms=round((time.perf_counter() - clock) * 1000, 1))
    record_event(
        session,
        tenant_id=tenant_id,
        event_type="reconciliation.run_completed",
        entity_type="reconciliation_run",
        entity_id=run.id,
        actor_type=ActorType.USER if started_by else ActorType.SYSTEM,
        actor_user_id=started_by,
        payload={"engine_version": ENGINE_VERSION, "import_batch_ids": run.import_batch_ids,
                 "matches": len(result.matches), "exceptions": len(result.exceptions),
                 "summary": result.summary},
    )
    return RunResult(run, created=True)


def _to_txn(t: Transaction, source_type) -> Txn:
    return Txn(
        id=t.id, source_id=t.source_id, source_type=source_type, source_transaction_id=t.source_transaction_id,
        external_reference=t.external_reference, transaction_date=t.transaction_date,
        transaction_timestamp=t.transaction_timestamp, amount=t.amount, fee_amount=t.fee_amount,
        currency=t.currency, direction=t.direction, counterparty=t.counterparty, merchant=t.merchant,
        status=t.status,
    )


def _default_key(batch_ids: list[uuid.UUID], config_snapshot: dict) -> str:
    body = json.dumps({"engine": ENGINE_VERSION, "batches": [str(b) for b in batch_ids], "config": config_snapshot},
                      sort_keys=True)
    return "auto:" + hashlib.sha256(body.encode()).hexdigest()


def _json_safe(value: dict) -> dict:
    return json.loads(json.dumps(value, default=str))

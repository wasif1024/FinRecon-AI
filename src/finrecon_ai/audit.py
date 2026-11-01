"""Append-only, per-tenant hash-chained audit log (FR-12)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.db.enums import ActorType
from finrecon_ai.db.models import AuditEvent


def record_event(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    entity_type: str,
    entity_id: Any,
    actor_type: ActorType,
    actor_user_id: uuid.UUID | None = None,
    agent_execution_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    # Serialize writers per tenant so the chain has no forks; released at commit/rollback.
    session.execute(select(func.pg_advisory_xact_lock(_lock_key(tenant_id))))
    prev_hash = session.scalar(
        select(AuditEvent.hash).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.id.desc()).limit(1)
    )

    event = AuditEvent(
        tenant_id=tenant_id,
        occurred_at=datetime.now(UTC),
        event_type=event_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        agent_execution_id=agent_execution_id,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        payload=json.loads(json.dumps(payload or {}, default=str)),
        request_id=request_id,
        prev_hash=prev_hash,
    )
    event.hash = compute_hash(event)
    session.add(event)
    session.flush()
    return event


def compute_hash(event: AuditEvent) -> str:
    body = {
        "tenant_id": str(event.tenant_id),
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
        "event_type": event.event_type,
        "actor_type": str(event.actor_type),
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "agent_execution_id": str(event.agent_execution_id) if event.agent_execution_id else None,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "payload": event.payload,
        "request_id": event.request_id,
        "prev_hash": event.prev_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_chain(session: Session, tenant_id: uuid.UUID) -> int | None:
    """Returns the id of the first tampered or out-of-chain event, or None if the chain is intact."""
    prev_hash = None
    events = session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.id))
    for event in events:
        if event.prev_hash != prev_hash or event.hash != compute_hash(event):
            return event.id
        prev_hash = event.hash
    return None


def _lock_key(tenant_id: uuid.UUID) -> int:
    return int.from_bytes(tenant_id.bytes[:8], "big", signed=True)

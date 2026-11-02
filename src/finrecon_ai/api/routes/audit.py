from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import AuditEventOut, ChainCheckOut
from finrecon_ai.audit import verify_chain
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.models import AuditEvent

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
def list_events(event_type: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
                before_id: int | None = Query(None, description="page backwards from this event id"),
                limit: int = Query(100, ge=1, le=500), actor: Actor = Depends(current_actor),
                session: Session = Depends(get_session)):
    actor.require(Permission.AUDIT_READ)
    query = select(AuditEvent).where(AuditEvent.tenant_id == actor.tenant_id)
    for column, value in ((AuditEvent.event_type, event_type), (AuditEvent.entity_type, entity_type),
                          (AuditEvent.entity_id, entity_id)):
        if value:
            query = query.where(column == value)
    if before_id:
        query = query.where(AuditEvent.id < before_id)
    return session.scalars(query.order_by(AuditEvent.id.desc()).limit(limit)).all()


@router.get("/verify", response_model=ChainCheckOut)
def verify(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    """Recomputes every hash in this tenant's audit chain."""
    actor.require(Permission.AUDIT_READ)
    bad = verify_chain(session, actor.tenant_id)
    count = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == actor.tenant_id))
    return ChainCheckOut(intact=bad is None, first_bad_event_id=bad, events_checked=count)

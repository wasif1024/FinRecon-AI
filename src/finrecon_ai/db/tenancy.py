"""Tenant scope for PostgreSQL row-level security (SRS §11, tenant-level data isolation).

Every tenant table has a forced RLS policy: a row is visible or writable only when its tenant_id equals the
transaction's `app.tenant_id` setting (migration 0005). The application binds a session to one tenant at its
chokepoints (loading the actor, ingestion, reconciliation, policies); the binding is re-applied at the start of
every transaction, so it survives commits. A session with no binding sees no tenant rows and cannot insert any:
forgetting to scope a query fails closed instead of leaking another tenant's data.

`bypass_rls` is for migrations and test setup only; application code never calls it.
"""

import uuid

from sqlalchemy import event, text
from sqlalchemy.orm import Session

_TENANT = "finrecon_tenant_id"
_BYPASS = "finrecon_rls_bypass"
_APPLY = text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.rls_bypass', :bypass, true)")


class TenantScopeError(Exception):
    pass


def bind_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Scope the session to one tenant. Rebinding to a different tenant is refused (call unbind first)."""
    current = session.info.get(_TENANT)
    if current is not None and current != str(tenant_id) and not session.info.get(_BYPASS):
        raise TenantScopeError("session is already bound to another tenant")
    session.info[_TENANT] = str(tenant_id)
    _apply_now(session)


def unbind(session: Session) -> None:
    session.info.pop(_TENANT, None)
    session.info.pop(_BYPASS, None)
    _apply_now(session)


def bypass_rls(session: Session) -> None:
    """See every tenant's rows. Migrations and test fixtures only."""
    session.info[_BYPASS] = True
    _apply_now(session)


def bound_tenant(session: Session) -> str | None:
    return session.info.get(_TENANT)


def _params(info: dict) -> dict[str, str]:
    return {"tenant": info.get(_TENANT, ""), "bypass": "on" if info.get(_BYPASS) else "off"}


def _apply_now(session: Session) -> None:
    if session.in_transaction():
        session.execute(_APPLY, _params(session.info))
    # otherwise the after_begin hook applies it when the next transaction starts


@event.listens_for(Session, "after_begin")
def _scope_new_transaction(session: Session, transaction, connection) -> None:
    connection.execute(_APPLY, _params(session.info))

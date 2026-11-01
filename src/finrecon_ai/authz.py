"""Role-based permissions (SRS §5, §11). Fail-closed: anything not granted here is denied.

Authentication arrives in Phase 4; services already take an Actor so they never trust caller-supplied roles.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from finrecon_ai.db.enums import RoleName
from finrecon_ai.db.models import User


class Permission(StrEnum):
    CASE_READ = "case:read"
    CASE_WORK = "case:work"  # move a case through investigation, add notes
    CASE_WORK_ANY = "case:work_any"  # ...including cases assigned to someone else
    CASE_ASSIGN = "case:assign"
    CASE_DECIDE = "case:decide"  # approve / reject (Phase 4 approval workflow)
    CASE_CREATE = "case:create"


ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.ANALYST: frozenset({Permission.CASE_READ, Permission.CASE_WORK}),
    RoleName.MANAGER: frozenset({Permission.CASE_READ, Permission.CASE_WORK, Permission.CASE_WORK_ANY,
                                 Permission.CASE_ASSIGN, Permission.CASE_DECIDE}),
    RoleName.ADMIN: frozenset({Permission.CASE_READ}),
    RoleName.AUDITOR: frozenset({Permission.CASE_READ}),
}
# The system actor runs automated steps (case creation after a reconciliation run). It cannot decide.
SYSTEM_PERMISSIONS = frozenset({Permission.CASE_READ, Permission.CASE_CREATE})


class PermissionDenied(Exception):
    pass


@dataclass(frozen=True)
class Actor:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    roles: frozenset[RoleName]

    @classmethod
    def system(cls, tenant_id: uuid.UUID) -> "Actor":
        return cls(tenant_id=tenant_id, user_id=None, roles=frozenset())

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    @property
    def permissions(self) -> frozenset[Permission]:
        if self.is_system:
            return SYSTEM_PERMISSIONS
        return frozenset().union(*(ROLE_PERMISSIONS.get(r, frozenset()) for r in self.roles))

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDenied(f"missing permission {permission}")


def load_actor(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Actor:
    """Roles always come from the database, never from the request."""
    user = session.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None or not user.is_active:
        raise PermissionDenied("unknown or inactive user")
    return Actor(tenant_id=tenant_id, user_id=user.id, roles=frozenset(r.name for r in user.roles))

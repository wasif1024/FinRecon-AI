import uuid

import pytest

from finrecon_ai.authz import Actor, Permission, PermissionDenied
from finrecon_ai.db.enums import RoleName

TENANT = uuid.uuid4()


def actor(*roles: RoleName) -> Actor:
    return Actor(tenant_id=TENANT, user_id=uuid.uuid4(), roles=frozenset(roles))


@pytest.mark.parametrize("role, permission, allowed", [
    (RoleName.ANALYST, Permission.CASE_WORK, True),
    (RoleName.ANALYST, Permission.CASE_ASSIGN, False),
    (RoleName.ANALYST, Permission.CASE_DECIDE, False),
    (RoleName.MANAGER, Permission.CASE_ASSIGN, True),
    (RoleName.MANAGER, Permission.CASE_DECIDE, True),
    (RoleName.AUDITOR, Permission.CASE_READ, True),
    (RoleName.AUDITOR, Permission.CASE_WORK, False),
    (RoleName.ADMIN, Permission.CASE_WORK, False),
])
def test_role_permissions(role, permission, allowed):
    assert actor(role).can(permission) is allowed


def test_no_roles_means_no_access():
    with pytest.raises(PermissionDenied):
        actor().require(Permission.CASE_READ)


def test_roles_combine():
    assert actor(RoleName.ANALYST, RoleName.AUDITOR).can(Permission.CASE_WORK)


def test_system_actor_creates_but_never_decides():
    system = Actor.system(TENANT)
    assert system.can(Permission.CASE_CREATE)
    assert not system.can(Permission.CASE_DECIDE)
    assert not system.can(Permission.CASE_WORK)


def test_unknown_role_grants_nothing():
    assert actor("superuser").permissions == frozenset()  # type: ignore[arg-type]

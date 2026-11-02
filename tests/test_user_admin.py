"""User administration over RPC: admins only, own tenant only, changes apply at once, the tenant stays administrable."""

import pytest
from connectrpc.code import Code
from sqlalchemy import select

from finrecon.v1 import identity_pb2 as ids
from finrecon.v1 import users_pb2 as us
from finrecon_ai.api.security import hash_password
from finrecon_ai.cli import DEMO_USERS
from finrecon_ai.db.enums import RoleName
from finrecon_ai.db.models import AuditEvent, Role, Tenant, User
from tests.test_api import PASSWORD, auth, client, scripts, store  # noqa: F401 (fixtures)
from tests.test_rpc import call, code_of, inline_background  # noqa: F401 (fixture)

NEW_PASSWORD = "a new long password"


def admin_call(client, method, request, response_type, headers=None):
    return call(client, "UserService", method, request, response_type, headers or auth(client, RoleName.ADMIN))


def login(client, email, password=NEW_PASSWORD):
    return call(client, "IdentityService", "Login", ids.LoginRequest(tenant="demo", email=email, password=password),
                ids.LoginResponse)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create(client, email="new.analyst@demo.finrecon.dev", roles=("analyst",)):
    return admin_call(client, "CreateUser", us.CreateUserRequest(email=email, full_name="New Analyst",
                                                                  roles=list(roles), password=NEW_PASSWORD),
                      us.CreateUserResponse).account


def test_only_admins_manage_users(client):
    accounts = admin_call(client, "ListAccounts", us.ListAccountsRequest(), us.ListAccountsResponse).accounts
    assert {a.email for a in accounts} >= {DEMO_USERS[RoleName.ANALYST][0], DEMO_USERS[RoleName.ADMIN][0]}
    for role in (RoleName.MANAGER, RoleName.ANALYST, RoleName.AUDITOR):
        assert code_of(lambda: admin_call(client, "ListAccounts", us.ListAccountsRequest(), us.ListAccountsResponse,
                                          auth(client, role))) == Code.PERMISSION_DENIED


def test_created_users_sign_in_and_role_changes_apply_to_issued_tokens(client):
    account = create(client)
    assert account.active and account.has_password and list(account.roles) == ["analyst"]
    token = login(client, account.email).access_token
    me = call(client, "IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse, bearer(token))
    assert "case:decide" not in me.permissions

    admin_call(client, "UpdateUser", us.UpdateUserRequest(user_id=account.id, roles=us.RoleSet(roles=["manager"])),
               us.UpdateUserResponse)
    me = call(client, "IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse, bearer(token))
    assert "case:decide" in me.permissions, "the same token, the new role"

    admin_call(client, "UpdateUser", us.UpdateUserRequest(user_id=account.id, active=False), us.UpdateUserResponse)
    assert code_of(lambda: call(client, "IdentityService", "GetMe", ids.GetMeRequest(), ids.GetMeResponse,
                                bearer(token))) == Code.UNAUTHENTICATED
    assert code_of(lambda: login(client, account.email)) == Code.UNAUTHENTICATED
    listed = {a.email: a for a in admin_call(client, "ListAccounts", us.ListAccountsRequest(),
                                             us.ListAccountsResponse).accounts}
    assert not listed[account.email].active, "deactivated users are still listed"


def test_invalid_requests_are_refused(client):
    create(client)

    def code(request):
        return code_of(lambda: admin_call(client, "CreateUser", request, us.CreateUserResponse))

    good = dict(email="x@demo.finrecon.dev", full_name="X", roles=["analyst"], password=NEW_PASSWORD)
    assert code(us.CreateUserRequest(**{**good, "email": "NEW.Analyst@demo.finrecon.dev"})) == \
        Code.FAILED_PRECONDITION, "emails are case-insensitive"
    assert code(us.CreateUserRequest(**{**good, "password": "short"})) == Code.INVALID_ARGUMENT
    assert code(us.CreateUserRequest(**{**good, "roles": ["superuser"]})) == Code.INVALID_ARGUMENT
    assert code(us.CreateUserRequest(**{**good, "roles": []})) == Code.INVALID_ARGUMENT
    assert code(us.CreateUserRequest(**{**good, "email": "not-an-email"})) == Code.INVALID_ARGUMENT


def test_the_tenant_stays_administrable(session, world, client):
    admin_id = str(world[2][RoleName.ADMIN].user_id)

    def update(**fields):
        return code_of(lambda: admin_call(client, "UpdateUser", us.UpdateUserRequest(user_id=admin_id, **fields),
                                          us.UpdateUserResponse))

    assert update(active=False) == Code.INVALID_ARGUMENT, "not yourself"
    assert update(roles=us.RoleSet(roles=["manager"])) == Code.INVALID_ARGUMENT, "not your own admin role"

    second = create(client, "second.admin@demo.finrecon.dev", roles=["admin"])
    other_admin = bearer(login(client, second.email).access_token)
    admin_call(client, "UpdateUser", us.UpdateUserRequest(user_id=admin_id, roles=us.RoleSet(roles=["manager"])),
               us.UpdateUserResponse, other_admin)
    assert code_of(lambda: admin_call(
        client, "UpdateUser", us.UpdateUserRequest(user_id=second.id, active=False), us.UpdateUserResponse,
        other_admin)) == Code.INVALID_ARGUMENT, "still not yourself, and the last admin"


def test_passwords_are_set_and_never_audited(session, world, client):
    analyst = world[2][RoleName.ANALYST]
    admin_call(client, "SetPassword", us.SetPasswordRequest(user_id=str(analyst.user_id), password=NEW_PASSWORD),
               us.SetPasswordResponse)
    assert login(client, DEMO_USERS[RoleName.ANALYST][0]).access_token
    assert code_of(lambda: login(client, DEMO_USERS[RoleName.ANALYST][0], PASSWORD)) == Code.UNAUTHENTICATED
    create(client)
    events = session.scalars(select(AuditEvent).where(AuditEvent.event_type.like("user.%"))).all()
    assert {e.event_type for e in events} >= {"user.password_set", "user.created"}
    assert all(NEW_PASSWORD not in str(e.payload) and "argon2" not in str(e.payload) for e in events)


def test_other_tenants_users_are_not_found(session, client):
    other = Tenant(name="Other", slug="other-admin")
    session.add(other)
    session.flush()
    outsider = User(tenant_id=other.id, email="x@other.dev", full_name="Outsider", password_hash=hash_password(PASSWORD),
                    roles=[session.scalar(select(Role).where(Role.name == RoleName.ANALYST))])
    session.add(outsider)
    session.flush()
    for request, method, response in (
            (us.UpdateUserRequest(user_id=str(outsider.id), active=False), "UpdateUser", us.UpdateUserResponse),
            (us.SetPasswordRequest(user_id=str(outsider.id), password=NEW_PASSWORD), "SetPassword",
             us.SetPasswordResponse)):
        assert code_of(lambda: admin_call(client, method, request, response)) == Code.NOT_FOUND
    listed = admin_call(client, "ListAccounts", us.ListAccountsRequest(), us.ListAccountsResponse).accounts
    assert "x@other.dev" not in {a.email for a in listed}


@pytest.mark.parametrize("field", ["email", "full_name"])
def test_blank_fields_are_refused(client, field):
    request = dict(email="y@demo.finrecon.dev", full_name="Y", roles=["analyst"], password=NEW_PASSWORD)
    request[field] = "   "
    assert code_of(lambda: admin_call(client, "CreateUser", us.CreateUserRequest(**request),
                                      us.CreateUserResponse)) == Code.INVALID_ARGUMENT

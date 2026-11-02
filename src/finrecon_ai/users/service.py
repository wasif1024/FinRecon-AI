"""User administration (SRS §5.3, §11): admins add colleagues, change their roles, deactivate them and set their
passwords, within their own tenant.

Roles are read from the database on every request, so a role change or deactivation applies to tokens already
issued. Guards keep the tenant administrable: an admin cannot deactivate themselves or drop their own admin role, and
the last active admin cannot be removed. Every change is audited (never a password or its hash).
"""

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from finrecon_ai.api.security import MIN_PASSWORD_LENGTH, hash_password
from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.enums import ActorType, RoleName
from finrecon_ai.db.models import Role, User
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.errors import NotFound
from finrecon_ai.log import get_logger

log = get_logger(__name__)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserError(Exception):
    pass


class UserConflict(UserError):
    pass


class UserNotFound(NotFound, UserError):
    pass


def list_accounts(session: Session, actor: Actor) -> list[User]:
    """Everyone in the tenant, deactivated users included."""
    actor.require(Permission.USER_MANAGE)
    bind_tenant(session, actor.tenant_id)
    return list(session.scalars(select(User).options(selectinload(User.roles))
                                .where(User.tenant_id == actor.tenant_id).order_by(User.full_name, User.email)))


def create_user(session: Session, actor: Actor, *, email: str, full_name: str, roles: list[str],
                password: str) -> User:
    actor.require(Permission.USER_MANAGE)
    bind_tenant(session, actor.tenant_id)
    email, full_name = email.strip().lower(), full_name.strip()
    if not EMAIL.match(email) or len(email) > 320:
        raise UserError("enter a valid email address")
    if not full_name or len(full_name) > 200:
        raise UserError("enter a name (up to 200 characters)")
    if session.scalar(select(User.id).where(User.tenant_id == actor.tenant_id, func.lower(User.email) == email)):
        raise UserConflict(f"{email} already has an account")
    user = User(tenant_id=actor.tenant_id, email=email, full_name=full_name, roles=_roles(session, roles),
                password_hash=_hash(password))
    session.add(user)
    session.flush()
    _audit(session, actor, user, "user.created", {"email": email, "roles": _names(user)})
    return user


def update_user(session: Session, actor: Actor, user_id: uuid.UUID, *, full_name: str | None = None,
                roles: list[str] | None = None, active: bool | None = None) -> User:
    actor.require(Permission.USER_MANAGE)
    user = _get(session, actor, user_id)
    if full_name is not None and full_name.strip() != user.full_name:
        name = full_name.strip()
        if not name or len(name) > 200:
            raise UserError("enter a name (up to 200 characters)")
        _audit(session, actor, user, "user.renamed", {"from": user.full_name, "to": name})
        user.full_name = name
    if roles is not None:
        new = _roles(session, roles)
        before, after = _names(user), sorted(r.name.value for r in new)
        if before != after:
            if user.id == actor.user_id and RoleName.ADMIN.value not in after:
                raise UserError("you cannot remove your own admin role; ask another admin")
            if RoleName.ADMIN.value in before and RoleName.ADMIN.value not in after:
                _keep_an_admin(session, actor, user)
            user.roles = new
            _audit(session, actor, user, "user.roles_changed", {"from": before, "to": after})
    if active is not None and active != user.is_active:
        if not active:
            if user.id == actor.user_id:
                raise UserError("you cannot deactivate yourself; ask another admin")
            if RoleName.ADMIN.value in _names(user):
                _keep_an_admin(session, actor, user)
        user.is_active = active
        _audit(session, actor, user, "user.reactivated" if active else "user.deactivated", {"email": user.email})
    session.flush()
    return user


def set_password(session: Session, actor: Actor, user_id: uuid.UUID, password: str) -> User:
    """An admin sets a user's password (a new colleague, a forgotten one). Tokens already issued stay valid until
    they expire; deactivate the user to cut them off at once."""
    actor.require(Permission.USER_MANAGE)
    user = _get(session, actor, user_id)
    user.password_hash = _hash(password)
    session.flush()
    _audit(session, actor, user, "user.password_set", {"email": user.email})
    return user


def _get(session: Session, actor: Actor, user_id: uuid.UUID) -> User:
    bind_tenant(session, actor.tenant_id)
    user = session.scalar(select(User).options(selectinload(User.roles))
                          .where(User.id == user_id, User.tenant_id == actor.tenant_id))
    if user is None:
        raise UserNotFound("user not found")
    return user


def _roles(session: Session, names: list[str]) -> list[Role]:
    try:
        wanted = {RoleName(n) for n in names}
    except ValueError:
        raise UserError(f"roles must be among: {', '.join(r.value for r in RoleName)}") from None
    if not wanted:
        raise UserError("give the user at least one role")
    return list(session.scalars(select(Role).where(Role.name.in_(wanted)).order_by(Role.name)))


def _names(user: User) -> list[str]:
    return sorted(r.name.value for r in user.roles)


def _hash(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"the password needs at least {MIN_PASSWORD_LENGTH} characters")
    return hash_password(password)


def _keep_an_admin(session: Session, actor: Actor, leaving: User) -> None:
    others = session.scalar(
        select(func.count()).select_from(User).join(User.roles)
        .where(User.tenant_id == actor.tenant_id, User.is_active, User.id != leaving.id, Role.name == RoleName.ADMIN))
    if not others:
        raise UserError("this is the last active admin; make someone else an admin first")


def _audit(session: Session, actor: Actor, user: User, event_type: str, payload: dict) -> None:
    record_event(session, tenant_id=actor.tenant_id, event_type=event_type, entity_type="user", entity_id=user.id,
                 actor_type=ActorType.USER, actor_user_id=actor.user_id, payload=payload)
    log.info(event_type, user_id=user.id)

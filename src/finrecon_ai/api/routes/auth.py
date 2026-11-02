from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import Runtime, current_actor, get_session, runtime
from finrecon_ai.api.schemas import LoginIn, MeOut, TokenOut
from finrecon_ai.api.security import create_token, verify_password
from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor
from finrecon_ai.db.enums import ActorType
from finrecon_ai.db.models import Tenant, User
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.log import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# One message for unknown tenant, unknown user, inactive user and wrong password: no account enumeration.
INVALID = "invalid credentials"


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, session: Session = Depends(get_session), rt: Runtime = Depends(runtime)) -> TokenOut:
    email = body.email.strip().lower()
    tenant = session.scalar(select(Tenant).where(Tenant.slug == body.tenant.strip().lower()))
    if tenant is not None:
        bind_tenant(session, tenant.id)
        if rt.throttle.blocked(session, tenant.id, email):
            log.warning("auth.login_throttled")
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts; try again later")
    user = session.scalar(select(User).where(User.tenant_id == tenant.id,
                                             func.lower(User.email) == email)) if tenant else None
    usable = user is not None and user.is_active
    # Always verify a hash so failures take as long as successes.
    if not verify_password(body.password, user.password_hash if usable else None) or not usable:
        if tenant is not None:
            record_event(session, tenant_id=tenant.id, event_type="auth.login_failed", entity_type="user",
                         entity_id=user.id if user else None, actor_type=ActorType.SYSTEM, payload={"email": email})
            session.commit()  # the failure must persist although the request fails: it feeds the lockout
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID)

    token, expires = create_token(user.id, tenant.id, rt.jwt_secret, rt.jwt_ttl_minutes)
    record_event(session, tenant_id=tenant.id, event_type="auth.login_succeeded", entity_type="user",
                 entity_id=user.id, actor_type=ActorType.USER, actor_user_id=user.id, payload={"email": email})
    return TokenOut(access_token=token, expires_at=expires)


@router.get("/me", response_model=MeOut)
def me(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)) -> MeOut:
    user = session.get(User, actor.user_id)
    return MeOut(user_id=user.id, tenant_id=actor.tenant_id, email=user.email, full_name=user.full_name,
                 roles=sorted(r.value for r in actor.roles), permissions=sorted(p.value for p in actor.permissions))

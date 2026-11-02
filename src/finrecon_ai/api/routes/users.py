from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import UserOut
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.models import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    """Colleagues in the caller's tenant: names for display and assignment. Never credentials."""
    actor.require(Permission.CASE_READ)
    users = session.scalars(select(User).options(selectinload(User.roles))
                            .where(User.tenant_id == actor.tenant_id, User.is_active).order_by(User.full_name))
    return [UserOut(id=u.id, email=u.email, full_name=u.full_name, roles=sorted(r.name.value for r in u.roles))
            for u in users]

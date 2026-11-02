"""Request-scoped dependencies: database session, authenticated actor, and the runtime services."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from finrecon_ai.api.security import InvalidToken, LoginThrottle, decode_token
from finrecon_ai.authz import Actor, PermissionDenied, load_actor
from finrecon_ai.policies.store import PolicyStore
from finrecon_ai.workflows.case_investigation import WorkflowDeps


@dataclass
class Runtime:
    """Everything the API needs beyond the request's own session. Tests swap these for fakes."""

    jwt_secret: str
    jwt_ttl_minutes: int
    unit_of_work: Callable[[], AbstractContextManager[Session]]
    workflow_deps: Callable[[], WorkflowDeps]
    checkpointer: Callable[[], AbstractContextManager[Any]]
    policy_store: Callable[[], PolicyStore]
    run_lock: Callable[[uuid.UUID], AbstractContextManager[bool]]
    throttle: LoginThrottle


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_session(rt: Runtime = Depends(runtime)) -> Iterator[Session]:
    """One transaction per request: committed if the endpoint succeeds, rolled back if it raises."""
    with rt.unit_of_work() as session:
        yield session


_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def current_actor(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
                  session: Session = Depends(get_session), rt: Runtime = Depends(runtime)) -> Actor:
    return authenticate(session, rt, credentials.credentials if credentials else None)


def authenticate(session: Session, rt: Runtime, token: str | None) -> Actor:
    """The caller behind an access token, for REST and RPC. Raises 401 for a missing, invalid or expired token."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated", headers=_UNAUTHORIZED)
    try:
        claims = decode_token(token, rt.jwt_secret)
        # Roles come from the database on every request, not from the token.
        return load_actor(session, claims.tenant_id, claims.user_id)
    except (InvalidToken, PermissionDenied):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token", headers=_UNAUTHORIZED) from None

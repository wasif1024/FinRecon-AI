import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import RunIn, RunOut
from finrecon_ai.authz import Actor, Permission
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.db.models import ReconciliationRun
from finrecon_ai.errors import NotFound
from finrecon_ai.reconciliation.service import run_reconciliation

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def start_run(body: RunIn, response: Response, actor: Actor = Depends(current_actor),
              session: Session = Depends(get_session)):
    actor.require(Permission.RECONCILE_RUN)
    result = run_reconciliation(session, tenant_id=actor.tenant_id, import_batch_ids=body.import_batch_ids,
                                started_by=actor.user_id)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    opened = None
    if body.open_cases:
        # Case creation is an automated step, done by the system actor (it cannot decide or work cases).
        opened = len(create_cases_for_run(session, Actor.system(actor.tenant_id), result.run.id).created)
    return RunOut.model_validate(result.run).model_copy(update={"cases_opened": opened})


@router.get("/runs", response_model=list[RunOut])
def list_runs(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.CASE_READ)
    return session.scalars(select(ReconciliationRun).where(ReconciliationRun.tenant_id == actor.tenant_id)
                           .order_by(ReconciliationRun.created_at.desc()).limit(50)).all()


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, actor: Actor = Depends(current_actor), session: Session = Depends(get_session)):
    actor.require(Permission.CASE_READ)
    run = session.scalar(select(ReconciliationRun).where(ReconciliationRun.id == run_id,
                                                         ReconciliationRun.tenant_id == actor.tenant_id))
    if run is None:
        raise NotFound("reconciliation run not found")
    return run

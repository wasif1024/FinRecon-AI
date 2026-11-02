from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from finrecon_ai.agents.metrics import agent_metrics
from finrecon_ai.api.deps import current_actor, get_session
from finrecon_ai.api.schemas import AgentMetricsOut
from finrecon_ai.authz import Actor

router = APIRouter(prefix="/metrics", tags=["monitoring"])


@router.get("/agents", response_model=list[AgentMetricsOut])
def agents(days: int = Query(30, ge=1, le=365), actor: Actor = Depends(current_actor),
           session: Session = Depends(get_session)):
    """Per agent and prompt version: reliability, latency, estimated cost, tool errors, grounding and outcomes."""
    return [AgentMetricsOut(**asdict(m), success_rate=m.success_rate) for m in agent_metrics(session, actor, days)]

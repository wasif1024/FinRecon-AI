"""LangGraph pipeline for SRS §8 steps 1-6: ingest -> reconcile -> open cases.

The Ingestion and Reconciliation agents (SRS §7.1, §7.2) are deterministic nodes: all financial work stays in
the existing services and no model is called. They are still recorded in agent_execution_records and audited,
so the full workflow reads the same way in the audit trail whichever steps involve a model.
"""

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from finrecon_ai.audit import record_event
from finrecon_ai.authz import Actor
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.db.enums import ActorType, AgentExecutionStatus, AgentName, ImportBatchStatus
from finrecon_ai.db.models import AgentExecutionRecord
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.reconciliation.config import ENGINE_VERSION
from finrecon_ai.reconciliation.service import run_reconciliation

log = get_logger(__name__)

PIPELINE_VERSION = "reconciliation_pipeline@1"


class PipelineState(TypedDict, total=False):
    tenant_id: str
    sources: list[dict[str, str]]  # [{"source_id": ..., "path": ...}]
    batch_ids: list[str]
    run_id: str
    cases_opened: int
    error: str | None


def build_pipeline_graph(unit_of_work: Callable[[], AbstractContextManager[Session]], checkpointer: Any = None):
    graph = StateGraph(PipelineState)
    graph.add_node("ingest", lambda s: _ingest(unit_of_work, s))
    graph.add_node("reconcile", lambda s: _reconcile(unit_of_work, s))
    graph.add_node("open_cases", lambda s: _open_cases(unit_of_work, s))
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", lambda s: END if s.get("error") else "reconcile", ["reconcile", END])
    graph.add_edge("reconcile", "open_cases")
    graph.add_edge("open_cases", END)
    return graph.compile(checkpointer=checkpointer)


def run_pipeline(unit_of_work: Callable[[], AbstractContextManager[Session]], tenant_id: uuid.UUID,
                 sources: list[tuple[uuid.UUID, Path]]) -> dict[str, Any]:
    with log_context(workflow=PIPELINE_VERSION, tenant_id=tenant_id):
        return dict(build_pipeline_graph(unit_of_work).invoke({
            "tenant_id": str(tenant_id), "error": None,
            "sources": [{"source_id": str(source_id), "path": str(path)} for source_id, path in sources]}))


def _ingest(unit_of_work, state: PipelineState) -> dict[str, Any]:
    tenant_id = uuid.UUID(state["tenant_id"])
    batch_ids, results = [], []
    with unit_of_work() as session, _scoped(session, tenant_id), _execution(session, tenant_id, AgentName.INGESTION, state["sources"]) as record:
        for source in state["sources"]:
            path = Path(source["path"])
            result = ingest_file(session, tenant_id=tenant_id, source_id=uuid.UUID(source["source_id"]),
                                 file_name=path.name, content=path.read_bytes(), storage_uri=str(path.resolve()))
            batch = result.batch
            results.append({"file": path.name, "batch_id": str(batch.id), "status": batch.status,
                            "accepted": batch.accepted_records, "rejected": batch.rejected_records,
                            "reused": not result.created})
            batch_ids.append(str(batch.id))
        record["output"] = {"batches": results}
        failed = [r["file"] for r in results if r["status"] == ImportBatchStatus.FAILED]
        if failed:
            record["error"] = f"files failed validation: {failed}"
            return {"batch_ids": batch_ids, "error": record["error"]}
    return {"batch_ids": batch_ids}


def _reconcile(unit_of_work, state: PipelineState) -> dict[str, Any]:
    tenant_id = uuid.UUID(state["tenant_id"])
    with unit_of_work() as session, _scoped(session, tenant_id), _execution(session, tenant_id, AgentName.RECONCILIATION,
                                               {"batch_ids": state["batch_ids"]}) as record:
        result = run_reconciliation(session, tenant_id=tenant_id,
                                    import_batch_ids=[uuid.UUID(b) for b in state["batch_ids"]])
        record["run_id"] = result.run.id
        record["output"] = {"run_id": str(result.run.id), "reused": not result.created, "summary": result.run.summary}
        return {"run_id": str(result.run.id)}


def _open_cases(unit_of_work, state: PipelineState) -> dict[str, Any]:
    tenant_id = uuid.UUID(state["tenant_id"])
    with unit_of_work() as session:
        created = create_cases_for_run(session, Actor.system(tenant_id), uuid.UUID(state["run_id"]))
        return {"cases_opened": len(created.created)}


@contextmanager
def _scoped(session: Session, tenant_id: uuid.UUID) -> Iterator[None]:
    bind_tenant(session, tenant_id)  # before the execution record is written: inserts need the tenant scope
    yield


class _execution:
    """Records a deterministic agent step in agent_execution_records, with its outcome and an audit event."""

    def __init__(self, session: Session, tenant_id: uuid.UUID, agent: AgentName, payload: Any):
        self.session, self.tenant_id, self.agent, self.payload = session, tenant_id, agent, payload
        self.values: dict[str, Any] = {}

    def __enter__(self) -> dict[str, Any]:
        self.started = time.perf_counter()
        self.row = AgentExecutionRecord(tenant_id=self.tenant_id, agent_name=self.agent, model=None,
                                        status=AgentExecutionStatus.RUNNING, input={"request": self.payload},
                                        workflow_version=f"{PIPELINE_VERSION}+engine@{ENGINE_VERSION}")
        self.session.add(self.row)
        self.session.flush()
        return self.values

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            return  # the unit of work rolls back; the error is logged by the caller
        row = self.row
        row.status = AgentExecutionStatus.FAILED if self.values.get("error") else AgentExecutionStatus.COMPLETED
        row.output, row.error = self.values.get("output"), self.values.get("error")
        row.run_id = self.values.get("run_id")
        row.completed_at = datetime.now(UTC)
        self.session.flush()
        duration = round((time.perf_counter() - self.started) * 1000, 1)
        log.info("pipeline.step_completed", agent=self.agent.value, status=row.status, duration_ms=duration)
        record_event(self.session, tenant_id=self.tenant_id, event_type=f"agent.execution_{row.status.value}",
                     entity_type="agent_execution", entity_id=row.id, actor_type=ActorType.SYSTEM,
                     agent_execution_id=row.id,
                     payload={"agent": self.agent.value, "deterministic": True, "duration_ms": duration,
                              "error": row.error})

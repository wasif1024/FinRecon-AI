"""Evaluation harness: isolated tenants, one fresh case per planted scenario, the real workflow.

Cases are consumed by investigating them, so each repetition of an experiment gets its own `eval-*` tenant from the
same deterministic synthetic data (and its own copy of the sample policies); `CasePool` hands every run a fresh case.
Row-level security keeps eval tenants apart from the demo tenant.

Runs go through the LangGraph Agent Server (`case-investigation`), the way Studio runs them. Results and the
trajectory (every tool call, subagents' included) are read back from the audited records, not from the stream.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from finrecon_ai.agents.llm import ChatModelFactory
from finrecon_ai.agents.workspace import StateWorkspaces, Workspaces
from finrecon_ai.authz import Actor, load_actor
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.cli import DEMO_SOURCES
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import EvidenceType, RoleName
from finrecon_ai.db.models import (
    AgentExecutionRecord,
    FinancialSource,
    InvestigationCase,
    InvestigationEvidence,
    InvestigationFinding,
    PolicyDocument,
    ReconciliationException,
    ResolutionRecommendation,
    Role,
    Tenant,
    Transaction,
    User,
    exception_transactions,
)
from finrecon_ai.db.tenancy import bind_tenant
from finrecon_ai.evals.scenarios import EXPECTATIONS
from finrecon_ai.ingestion.mappings import DEFAULT_MAPPINGS
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.policies.service import upload_policy
from finrecon_ai.policies.store import PolicyStore
from finrecon_ai.reconciliation.service import run_reconciliation
from finrecon_ai.synthetic.generator import Scenario, generate, write_dataset
from finrecon_ai.workflows.case_investigation import WorkflowDeps, investigate_case

EVAL_SEED = 3
EVAL_PAYMENTS = 250  # small, but every scenario is planted at least once
POLICIES_DIR = Path(__file__).resolve().parents[3] / "data" / "policies"


@dataclass(frozen=True)
class EvalTenant:
    tenant_id: uuid.UUID
    manager_id: uuid.UUID
    cases: dict[str, uuid.UUID]  # scenario -> case id
    manifest: dict[str, Any]
    slug: str = ""
    case_numbers: dict[str, str] = field(default_factory=dict)  # scenario -> case number


class CasePool:
    """A fresh case for every run: repetitions of a scenario take its case from successive tenants."""

    def __init__(self, tenants: list[EvalTenant]):
        self._lock = threading.Lock()
        self._free = {scenario: [(t, t.cases[scenario]) for t in tenants if scenario in t.cases]
                      for scenario in {s for t in tenants for s in t.cases}}

    def take(self, scenario: str) -> tuple[EvalTenant, uuid.UUID]:
        with self._lock:
            if not self._free.get(scenario):
                raise RuntimeError(f"no fresh case left for {scenario}: prepare one tenant per repetition")
            return self._free[scenario].pop(0)


def prepare_tenant(unit_of_work, store: PolicyStore, workdir: Path) -> EvalTenant:
    dataset = generate(seed=EVAL_SEED, payments=EVAL_PAYMENTS)
    write_dataset(dataset, workdir)
    with unit_of_work() as session:
        slug = f"eval-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        tenant = Tenant(name="Evaluation", slug=slug)
        session.add(tenant)
        session.flush()
        bind_tenant(session, tenant.id)
        roles = {r.name: r for r in session.scalars(select(Role))}
        # The Agent Server acts as STUDIO_ACTOR_EMAIL in the tenant a run names.
        manager = User(tenant_id=tenant.id, email=get_settings().studio_actor_email, full_name="Eval Manager",
                       roles=[roles[RoleName.MANAGER]])
        admin = User(tenant_id=tenant.id, email="eval-admin@eval.local", full_name="Eval Admin",
                     roles=[roles[RoleName.ADMIN]])
        session.add_all([manager, admin])
        batch_ids = []
        for name, source_type, method, file_name in DEMO_SOURCES.values():
            source = FinancialSource(tenant_id=tenant.id, name=name, source_type=source_type,
                                     ingestion_method=method,
                                     config={"mapping": DEFAULT_MAPPINGS[source_type].model_dump(mode="json")})
            session.add(source)
            session.flush()
            batch_ids.append(ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=file_name,
                                         content=(workdir / file_name).read_bytes()).batch.id)
        run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids).run
        create_cases_for_run(session, Actor.system(tenant.id), run.id)
        admin_actor = load_actor(session, tenant.id, admin.id)
        for path in sorted(POLICIES_DIR.iterdir()):
            upload_policy(session, admin_actor, store, file_name=path.name, content=path.read_bytes(),
                          storage_dir=workdir / "storage")
        cases = _case_per_scenario(session, tenant.id, dataset.manifest)
        numbers = dict(session.execute(select(InvestigationCase.id, InvestigationCase.case_number)
                                       .where(InvestigationCase.id.in_(cases.values()))).all())
        return EvalTenant(tenant.id, manager.id, cases, dataset.manifest, slug,
                          {scenario: numbers[case_id] for scenario, case_id in cases.items()})


def _case_per_scenario(session, tenant_id: uuid.UUID, manifest: dict) -> dict[str, uuid.UUID]:
    rows = session.execute(
        select(InvestigationCase.id, ReconciliationException.category, Transaction.source_transaction_id)
        .join(ReconciliationException, ReconciliationException.id == InvestigationCase.exception_id)
        .join(exception_transactions, exception_transactions.c.exception_id == ReconciliationException.id)
        .join(Transaction, Transaction.id == exception_transactions.c.transaction_id)
        .where(InvestigationCase.tenant_id == tenant_id)).all()
    by_case: dict[uuid.UUID, tuple[str, set[str]]] = {}
    for case_id, category, source_id in rows:
        by_case.setdefault(case_id, (category, set()))[1].add(source_id)
    chosen: dict[str, uuid.UUID] = {}
    for entry in manifest["scenarios"]:
        scenario = Scenario(entry["scenario"])
        if scenario not in EXPECTATIONS or scenario.value in chosen:
            continue
        ids = set(entry["payment_ids"]) | set(entry["settlement_ids"]) | set(entry["bank_transaction_ids"])
        expected = EXPECTATIONS[scenario].category
        for case_id, (category, case_ids) in sorted(by_case.items(), key=lambda kv: str(kv[0])):
            if category == expected and case_ids <= ids:
                chosen[scenario.value] = case_id
                break
    return chosen


def scenario_details(manifest: dict, scenario: str) -> dict[str, Any]:
    entry = next((e for e in manifest["scenarios"] if e["scenario"] == scenario), {})
    return entry.get("details", {})


def investigate(unit_of_work, llm: ChatModelFactory, store: PolicyStore, tenant: EvalTenant, scenario: str,
                workspaces: Workspaces | None = None) -> dict[str, Any]:
    """Run the real workflow on the scenario's case and collect what the evaluators need."""
    case_id = tenant.cases[scenario]
    with unit_of_work() as session:
        actor = load_actor(session, tenant.tenant_id, tenant.manager_id)
    deps = WorkflowDeps(unit_of_work=unit_of_work, llm=llm, policy_store=store,
                        workspaces=workspaces or StateWorkspaces())
    result = investigate_case(deps, InMemorySaver(), actor, case_id)
    return collect(unit_of_work, tenant.tenant_id, case_id, result.state, result.paused_for_approval)


def investigate_on_server(agent_server, unit_of_work, tenant: EvalTenant, case_id: uuid.UUID,
                          scenario: str) -> dict[str, Any]:
    """Run `case-investigation` on the Agent Server (as Studio does) and collect the result from the records."""
    from finrecon_ai.agent_server import CASE_GRAPH_ID

    thread = agent_server.threads.create(metadata={"eval_tenant": tenant.slug, "scenario": scenario})
    agent_server.runs.wait(thread["thread_id"], CASE_GRAPH_ID, raise_error=False,
                           input={"case_number": tenant_case_number(tenant, case_id), "tenant_slug": tenant.slug})
    snapshot = agent_server.threads.get_state(thread["thread_id"])
    state = snapshot.get("values") or {}
    error = state.get("error") or next((str(t["error"]) for t in snapshot.get("tasks", []) if t.get("error")), None)
    paused = bool(snapshot.get("next"))
    out = collect(unit_of_work, tenant.tenant_id, case_id, {**state, "error": error}, paused and not error)
    out["thread_id"] = thread["thread_id"]
    return out


WORKFLOW_ORDER = ("investigation", "risk_analysis", "resolution")


def _workflow_order(execution: AgentExecutionRecord) -> tuple:
    """When the agents ran; ties (one transaction shares now()) in the workflow's order."""
    name = execution.agent_name.value
    return execution.started_at, WORKFLOW_ORDER.index(name) if name in WORKFLOW_ORDER else len(WORKFLOW_ORDER)


def tenant_case_number(tenant: EvalTenant, case_id: uuid.UUID) -> str:
    return next(n for s, n in tenant.case_numbers.items() if tenant.cases[s] == case_id)


def collect(unit_of_work, tenant_id: uuid.UUID, case_id: uuid.UUID, state: dict, paused: bool) -> dict[str, Any]:
    with unit_of_work() as session:
        bind_tenant(session, tenant_id)
        recommendation = session.scalar(select(ResolutionRecommendation)
                                        .where(ResolutionRecommendation.case_id == case_id)
                                        .order_by(ResolutionRecommendation.version.desc()).limit(1))
        findings = session.scalars(select(InvestigationFinding).where(InvestigationFinding.case_id == case_id)).all()
        policy_titles = session.scalars(
            select(PolicyDocument.title).join(InvestigationEvidence,
                                              InvestigationEvidence.policy_document_id == PolicyDocument.id)
            .where(InvestigationEvidence.case_id == case_id,
                   InvestigationEvidence.evidence_type == EvidenceType.POLICY_PASSAGE)).all()
        executions = session.scalars(select(AgentExecutionRecord).where(AgentExecutionRecord.case_id == case_id)).all()
        return {
            "completed": paused and not state.get("error"),
            "error": state.get("error"),
            "resolution_type": recommendation.resolution_type.value if recommendation else None,
            "summary": recommendation.summary if recommendation else None,
            "rationale": recommendation.rationale if recommendation else None,
            "action_steps": (recommendation.proposed_action or {}).get("action_steps", []) if recommendation else [],
            "findings": [{"type": f.finding_type.value, "statement": f.statement} for f in findings],
            "policies_cited": sorted(set(policy_titles)),
            "tools_used": sorted({c["tool"] for e in executions for c in (e.tool_calls or [])}),
            # In order, per agent; subagents' calls carry their name. Read from the audited execution records.
            "trajectory": [{"agent": e.agent_name.value, "tool": c["tool"], "subagent": c.get("subagent"),
                            "path": (c.get("input") or {}).get("file_path"), "ok": c.get("ok", True)}
                           for e in sorted(executions, key=_workflow_order) for c in (e.tool_calls or [])],
            "rejected_output": state.get("rejected_output", []),
            "agents": sorted({e.agent_name.value for e in executions}),
            "input_tokens": sum(e.input_tokens or 0 for e in executions),
            "output_tokens": sum(e.output_tokens or 0 for e in executions),
        }

